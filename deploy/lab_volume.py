#!/usr/bin/env python3
"""Root-owned bridge for the lab volume. No automount, format or reboot actions.

Installed as /usr/local/sbin/photogrammetry-volume; configuration is root-owned
/etc/photogrammetry/node.json. Only status/open/close are accepted.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


def run(args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=120)


def config():
    path = Path('/etc/photogrammetry/node.json')
    info = path.stat()
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Node configuration must be root-owned and not group/world writable')
    cfg = json.loads(path.read_text())
    if cfg['mountpoint'] != '/data' or not cfg['tag'].startswith('L507_'):
        raise RuntimeError('Unsupported node configuration')
    return cfg


def mounted(cfg):
    result = run(['findmnt', '-J', '--mountpoint', cfg['mountpoint']], check=False)
    if result.returncode == 1:
        return False
    if result.returncode:
        raise RuntimeError(result.stderr)
    entry = json.loads(result.stdout)['filesystems'][0]
    source = os.path.realpath(entry['source'])
    expected = os.path.realpath('/dev/disk/by-uuid/' + cfg['data_uuid'])
    if source != expected:
        raise RuntimeError('/data is occupied by another filesystem')
    if (Path(cfg['mountpoint']) / 'photogrammetry/NODE_TAG.txt').read_text().strip() != cfg['tag']:
        raise RuntimeError('Mounted tag does not match enrolled node')
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'open', 'close'])
    args = parser.parse_args()
    cfg = config()
    if args.action != 'status' and os.geteuid() != 0:
        parser.error('open/close require sudo')
    device = '/dev/disk/by-uuid/' + cfg['data_uuid']
    if args.action == 'open':
        if not mounted(cfg):
            info = json.loads(run(['lsblk','-bJ','-o','SIZE,FSTYPE',device]).stdout)['blockdevices'][0]
            if info['fstype'] != 'ext4' or not 400 * 1024**3 < info['size'] < 600 * 1024**3:
                raise RuntimeError('Unexpected data partition')
            target = Path(cfg['mountpoint'])
            if any(target.iterdir()):
                raise RuntimeError('Unmounted /data is not empty; refusing to hide files')
            run(['mount','-t','ext4','-o','nodev,nosuid',device,cfg['mountpoint']])
            try:
                mounted(cfg)
            except BaseException:
                run(['umount', cfg['mountpoint']])
                raise
        run(['systemctl','start','containerd.service','docker.service'])
        expected = '/data/photogrammetry/runtime/docker'
        actual = run(['docker','info','--format','{{.DockerRootDir}}']).stdout.strip()
        if actual != expected:
            raise RuntimeError('Docker is not using the data volume')
    elif args.action == 'close':
        if mounted(cfg):
            if run(['pgrep', '-af', r'^docker (build|buildx)'], check=False).returncode == 0:
                raise RuntimeError('Docker build is active; wait before closing the volume')
            if run(['systemctl','is-active','--quiet','docker.service'],check=False).returncode == 0:
                if run(['docker','ps','-q']).stdout.strip():
                    raise RuntimeError('Containers are running; finish/cancel jobs before closing')
            run(['systemctl','stop','docker.socket','docker.service','containerd.service'])
            # No forced/lazy unmount: open files must be dealt with explicitly.
            run(['umount',cfg['mountpoint']])
    print(json.dumps({'tag':cfg['tag'],'mounted':mounted(cfg),
                      'docker':run(['systemctl','is-active','docker.service'],check=False).stdout.strip()}))


if __name__ == '__main__':
    main()
