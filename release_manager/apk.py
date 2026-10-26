"""Strict APK structural checks and Android SDK signing metadata (no malware scanning)."""
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile


class InvalidApk(ValueError):
    pass


def structural_check(path: Path):
    try:
        with zipfile.ZipFile(path) as apk:
            infos = apk.infolist()
            names = {i.filename for i in infos}
            if len(infos) > 5000 or 'AndroidManifest.xml' not in names or not any(
                name == 'classes.dex' or re.fullmatch(r'classes[0-9]+\.dex', name) for name in names
            ):
                raise InvalidApk('APK archive is missing required Android entries')
            if any(i.flag_bits & 1 for i in infos):
                raise InvalidApk('Encrypted APK entries are not accepted')
            if sum(i.file_size for i in infos) > 300 * 1024 * 1024:
                raise InvalidApk('APK decompressed contents exceed limit')
            if apk.testzip() is not None:
                raise InvalidApk('Corrupted APK entry')
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise InvalidApk('Invalid ZIP/APK archive') from exc


def verified_metadata(path: Path, apksigner: str, aapt: str):
    """Fails closed if tools aren't installed or signer is invalid."""
    structural_check(path)
    try:
        signed = subprocess.run([apksigner, 'verify', '--print-certs', str(path)],
                                capture_output=True, text=True, timeout=60, check=True)
        badging = subprocess.run([aapt, 'dump', 'badging', str(path)],
                                capture_output=True, text=True, timeout=60, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidApk('APK signature/metadata verification is unavailable or failed') from exc
    m = re.search(r"^package:\s+name='([^']+)'[^\n]*versionName='([^']*)'", badging.stdout, re.M)
    signer = re.search(r'Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F:]{64,95})', signed.stdout)
    if not m or not signer:
        raise InvalidApk('Could not establish package identity and valid signing certificate')
    return {'package_name': m.group(1), 'version_name': m.group(2),
            'signer_sha256': signer.group(1).replace(':', '').lower()}


def digest_file(path: Path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
