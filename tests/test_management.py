import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from release_manager.settings import Settings
from release_manager.web import create_app

H = {'X-Internal-Token': 'X' * 40}


@pytest.fixture
def client(tmp_path):
    template_dir = tmp_path / 'owner_templates'
    template_dir.mkdir()
    (template_dir / 'registry.json').write_text('[]')
    # Test fixture only: no templates are provided with the actual product.
    for name in ('my-site', 'my-second-site'):
        folder = template_dir / name
        folder.mkdir()
        (folder / 'index.html').write_text(
            '<!doctype html><html><head><title>Template</title></head><body>'
            '<h1 data-release-title>Title</h1><a data-apk-install>Install</a>'
            '<div data-release-package></div><script src="asset.js"></script></body></html>'
        )
        (folder / 'asset.js').write_text('window.templateAssetLoaded = true;')
    cfg = Settings(database_url='sqlite:///' + str(tmp_path / 'test.sqlite'),
                   internal_api_token='X' * 40, key_pepper='Y' * 40,
                   admin_telegram_ids='100', private_upload_dir=str(tmp_path / 'apks'),
                   owner_template_dir=str(template_dir), max_upload_mb=1,
                   apksigner_path='missing-apksigner-test', public_base_url='')
    with TestClient(create_app(cfg)) as c:
        yield c, template_dir


def post(c, endpoint, **payload):
    return c.post('/internal/' + endpoint, json=payload, headers=H)


def grant(c, uid=200):
    response = post(c, 'grant', telegram_id=100, target_id=uid, days=30, slots=3)
    assert response.status_code == 200, response.text


def slot(c, uid=200, template='my-site'):
    result = post(c, 'slots', telegram_id=uid, template_id=template)
    assert result.status_code == 200, result.text
    return result.json()


def fake_apk():
    buf = io.BytesIO()
    with ZipFile(buf, 'w') as z:
        z.writestr('AndroidManifest.xml', b'test manifest')
        z.writestr('classes.dex', b'test code')
    return buf.getvalue()


def upload(c, slot_id, uid=200, payload=None):
    return c.post('/internal/apk/upload', headers=H,
                  data={'telegram_id': uid, 'slot_id': slot_id},
                  files={'document': ('app.apk', fake_apk() if payload is None else payload,
                                      'application/vnd.android.package-archive')})


def test_no_prefabricated_templates(client):
    c, templates = client
    assert post(c, 'templates', telegram_id=200).json()['templates'] == []
    assert post(c, 'slots', telegram_id=200, template_id='minimal').status_code == 400
    assert post(c, 'slots', telegram_id=200, template_id='my-site').status_code == 400
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'Your Own Website'}]))
    assert post(c, 'templates', telegram_id=200).json()['templates'] == [
        {'id': 'my-site', 'label': 'Your Own Website'}]


def test_redeem_then_template_choice(client):
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'Only Owner'}]))
    assert post(c, 'setdefaults', telegram_id=100, slots=1, apks=2, storage_mb=100).status_code == 200
    key = post(c, 'issue', telegram_id=100, days=30, slots=1, uses=1).json()['key']
    assert post(c, 'redeem', telegram_id=200, key=key).status_code == 200
    s = slot(c)
    assert post(c, 'slots', telegram_id=200, template_id='my-site').status_code == 403
    assert post(c, 'redeem', telegram_id=201, key=key).status_code == 400
    assert post(c, 'slots/template', telegram_id=200, slot_id=s['id'], template_id='fake').status_code == 400


def test_auto_link_without_manual_approval(client, monkeypatch):
    from release_manager import web
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'Only Owner'}]))
    grant(c)
    s = slot(c)
    # The pre-upload URL is a placeholder, and must not have an active download.
    assert c.get(s['path']).status_code == 200
    assert '"download": ""' in c.get(s['path']).text
    assert c.get(f'/p/{s["slug"]}/download').status_code == 404
    # Fail closed when Android SDK signature checker is unavailable.
    assert upload(c, s['id']).status_code == 422
    monkeypatch.setattr(web, 'verified_metadata', lambda *args: {
        'package_name': 'org.example.developerapp', 'version_name': '1.2',
        'signer_sha256': 'a' * 64,
    })
    result = upload(c, s['id'])
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['state'] == 'approved'
    assert data['url'] is None
    assert data['domain_configured'] is False
    assert c.get(s['path']).status_code == 404
    html = c.get(data['path']).text
    assert 'data-apk-install' in html
    assert '/download?t=' in html
    assert 'org.example.developerapp' in html
    url = data['path'].replace('?', '/download?', 1)
    download = c.get(url)
    assert download.status_code == 200
    assert download.content == fake_apk()
    assert post(c, 'apk/revoke', telegram_id=200, release_id=data['id']).status_code == 403
    assert post(c, 'apk/revoke', telegram_id=100, release_id=data['id']).status_code == 200
    assert c.get(url).status_code == 404


def test_quota_changes_and_domain(client):
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'Your Site'}]))
    grant(c)
    grant(c, 201)
    assert post(c, 'setquota', telegram_id=100, target_id=200, slots=0, apks=0,
                storage_mb=0).status_code == 200
    assert post(c, 'slots', telegram_id=200, template_id='my-site').status_code == 403
    bulk = post(c, 'setallquota', telegram_id=100, slots=1, apks=1, storage_mb=50)
    assert bulk.json()['users_updated'] == 2
    assert post(c, 'setdefaults', telegram_id=100, slots=5, apks=6,
                storage_mb=200).status_code == 200
    assert post(c, 'setdomain', telegram_id=200,
                url='https://releases.example.com').status_code == 403
    assert post(c, 'setdomain', telegram_id=100,
                url='http://releases.example.com').status_code == 400
    assert post(c, 'setdomain', telegram_id=100,
                url='https://releases.example.com/path').status_code == 400
    assert post(c, 'setdomain', telegram_id=100,
                url='https://releases.example.com').status_code == 200


def test_template_assets_and_paths(client):
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'My Design'}]))
    grant(c)
    s = slot(c)
    assert c.get('/template-assets/my-site/asset.js').status_code == 200
    assert c.get('/template-assets/my-site/../../../../etc/passwd').status_code in (400, 404)
    assert c.get('/template-assets/my-site/index.html').status_code == 404
    assert 'Independent developer APK' in c.get(s['path']).text
    assert post(c, 'slots/disable', telegram_id=200, slot_id=s['id']).status_code == 200
    assert c.get(s['path']).status_code == 404


def test_user_isolation_and_bad_upload(client):
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([{'id': 'my-site', 'label': 'My Design'}]))
    grant(c)
    grant(c, 201)
    s = slot(c)
    assert upload(c, s['id'], uid=201).status_code == 422  # APK verification happens first
    assert post(c, 'slots/disable', telegram_id=201, slot_id=s['id']).status_code == 404
    assert upload(c, s['id'], payload=b'not-an-apk').status_code == 422
    assert c.post('/internal/status', json={'telegram_id': 200}).status_code == 401


def test_full_domain_link_and_independent_users(client, monkeypatch):
    from release_manager import web
    c, templates = client
    (templates / 'registry.json').write_text(json.dumps([
        {'id': 'my-site', 'label': 'Owner Website 1'},
        {'id': 'my-second-site', 'label': 'Owner Website 2'}]))
    grant(c, 200)
    grant(c, 201)
    one = slot(c, 200, 'my-site')
    two = slot(c, 201, 'my-second-site')
    assert one['slug'] != two['slug']
    assert post(c, 'setdomain', telegram_id=100,
                url='https://releases.example.test').status_code == 200
    monkeypatch.setattr(web, 'verified_metadata', lambda *args: {
        'package_name': 'org.creator.demo', 'version_name': '1.0',
        'signer_sha256': 'b' * 64,
    })
    result = upload(c, one['id']).json()
    assert result['domain_configured']
    assert result['url'] == 'https://releases.example.test' + result['path']
    assert c.get(result['path']).status_code == 200
    assert c.get(two['path']).status_code == 200
    assert post(c, 'suspend', telegram_id=100, target_id=200, value=True).status_code == 200
    assert c.get(result['path']).status_code == 404
    assert c.get(two['path']).status_code == 200
