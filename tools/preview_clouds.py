#!/usr/bin/env python3
"""Inspect sparse/dense binary PLY files and render lightweight PCA previews.

Handles OpenMVS per-vertex view_indices/view_weights lists without Open3D.
The PLY files and their coordinate systems are never modified.
"""
import argparse
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image, ImageDraw

TYPES = {'float':'f','float32':'f','double':'d','float64':'d',
         'uchar':'B','uint8':'B','char':'b','int8':'b','uint':'I','uint32':'I',
         'int':'i','int32':'i','ushort':'H','uint16':'H','short':'h','int16':'h'}


def read_cloud(path, max_points=200000, max_bytes=512*1024**2):
    if max_points < 1 or path.stat().st_size > max_bytes:
        raise ValueError('Preview input or sample limit exceeded')
    with path.open('rb') as f:
        if f.readline().strip() != b'ply':
            raise ValueError('Not a PLY file')
        properties, count, current, format_name = [], None, None, None
        for _ in range(1000):
            line = f.readline().decode('ascii').strip()
            parts = line.split()
            if line == 'end_header':
                break
            if parts[:1] == ['format']:
                format_name = parts[1]
            elif parts[:1] == ['element']:
                current = parts[1]
                if current == 'vertex':
                    count = int(parts[2])
            elif parts[:1] == ['property'] and current == 'vertex':
                properties.append(parts[1:])
        else:
            raise ValueError('PLY header too long')
        if format_name != 'binary_little_endian' or count is None or count <= 0:
            raise ValueError('Expected a nonempty little-endian binary point cloud')
        data = memoryview(f.read())
    scalars, lists = [], []
    for prop in properties:
        if prop[0] == 'list':
            lists.append((struct.Struct('<'+TYPES[prop[1]]), struct.calcsize(TYPES[prop[2]])))
        elif lists:
            raise ValueError('Unsupported scalar property after list property')
        else:
            scalars.append(prop)
    names = [p[-1] for p in scalars]
    indices = [names.index(name) for name in ('x','y','z','red','green','blue')]
    prefix = struct.Struct('<' + ''.join(TYPES[p[0]] for p in scalars))
    stride = max(1, math.ceil(count / max_points))
    sample = np.empty((math.ceil(count / stride), 6), dtype=np.float64)
    offset = slot = 0
    for i in range(count):
        if offset + prefix.size > len(data):
            raise ValueError('Truncated PLY vertex data')
        if i % stride == 0:
            values = prefix.unpack_from(data, offset)
            sample[slot] = [values[k] for k in indices]
            slot += 1
        offset += prefix.size
        for counter, item_size in lists:
            if offset + counter.size > len(data):
                raise ValueError('Truncated PLY list count')
            length = counter.unpack_from(data, offset)[0]
            offset += counter.size + length * item_size
            if offset > len(data):
                raise ValueError('Truncated PLY list payload')
    if not np.isfinite(sample[:,:3]).all():
        raise ValueError('Non-finite point coordinates in preview sample')
    return sample, {'vertices':count, 'sampled_vertices':len(sample), 'bytes':path.stat().st_size,
                    'properties':[p[-1] for p in properties],
                    'sample_bbox_min':sample[:,:3].min(axis=0).tolist(),
                    'sample_bbox_max':sample[:,:3].max(axis=0).tolist()}


def render(sample, title, output):
    xyz = sample[:,:3]
    rgb = sample[:,3:].clip(0,255).astype(np.uint8)
    centered = xyz - np.median(xyz,axis=0)
    _, axes = np.linalg.eigh(np.cov(centered.T))
    points = centered @ axes[:,::-1]
    canvas = Image.new('RGB',(1920,560),(18,22,27))
    draw = ImageDraw.Draw(canvas)
    draw.text((12,10), title + ' - PCA projections, arbitrary scale (preview only)',fill='white')
    for view,(x,y,z) in enumerate([(0,1,2),(0,2,1),(1,2,0)]):
        bounds=np.percentile(points[:,[x,y]],[0.5,99.5],axis=0)
        scale=min(610/max(bounds[1,0]-bounds[0,0],1e-9),475/max(bounds[1,1]-bounds[0,1],1e-9))
        u=((points[:,x]-bounds[0,0])*scale+15).astype(int)
        v=(520-(points[:,y]-bounds[0,1])*scale).astype(int)
        keep=(u>=0)&(u<640)&(v>=45)&(v<540)
        order=np.argsort(points[keep,z])
        raster=np.full((560,640,3),(18,22,27),dtype=np.uint8)
        raster[v[keep][order],u[keep][order]]=rgb[keep][order]
        canvas.paste(Image.fromarray(raster),(view*640,0))
    draw=ImageDraw.Draw(canvas)
    draw.text((12,10),title+' - PCA projections, arbitrary scale (preview only)',fill='white')
    canvas.save(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',type=Path,required=True)
    args=parser.parse_args()
    out=args.run_dir/'previews/clouds'
    out.mkdir(parents=True,exist_ok=True)
    report={}
    for name,relative in [('sparse','colmap/sparse.ply'),('dense','mvs/scene_dense.ply')]:
        sample,info=read_cloud(args.run_dir/relative)
        render(sample,f'{name}: {info["vertices"]:,} points',out/(name+'.png'))
        report[name]=info
    frames=sorted((args.run_dir/'frames').rglob('*.jpg'))
    if frames:
        contact=Image.new('RGB',(1280,400),(18,22,27))
        for i,path in enumerate([frames[0],frames[-1]]):
            with Image.open(path) as im:
                im=im.convert('RGB'); im.thumbnail((640,360)); contact.paste(im,(i*640,30))
            ImageDraw.Draw(contact).text((i*640+8,8),path.name,fill='white')
        contact.save(out/'input-frames.png')
    (out/'cloud-inspection.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
