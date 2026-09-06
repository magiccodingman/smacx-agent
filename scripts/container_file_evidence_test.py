#!/usr/bin/env python3
"""Bounded archive reads reject links, oversized files and extra members."""
import io
import json
import tarfile
from smacx_docker import DockerClient, DockerError


def bundle(kind='file', size=4, extra=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        entry = tarfile.TarInfo('debug.txt')
        if kind == 'link':
            entry.type = tarfile.SYMTYPE
            entry.linkname = '/etc/shadow'
        else:
            entry.size = size
        archive.addfile(entry, io.BytesIO(b'x' * size) if kind == 'file' else None)
        if extra:
            archive.addfile(tarfile.TarInfo('unexpected'))
    return output.getvalue()


def main():
    client = DockerClient()
    for kind, size, extra, accepted in [('file',4,False,True),
            ('link',4,False,False), ('file',9,False,False), ('file',4,True,False)]:
        client._request = lambda *a, **k: (200, bundle(kind,size,extra))
        try:
            result = client.read_container_file('owned-worker', '/game/debug.txt', max_bytes=8)
        except DockerError:
            assert not accepted
        else:
            assert accepted and result == b'xxxx'
    print(json.dumps({'event':'pass','bounded_regular_file_only':True}))

if __name__ == '__main__':
    main()
