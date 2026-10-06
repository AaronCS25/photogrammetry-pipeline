"""Precomputa manzanas.geojson y lotes.geojson para el mapa (se ejecuta una vez).

Agrupa lotes contiguos (a menos de 10 cm) igual que barranco-streetview
(gis.py) y barranco-studio (import_gis.py), con el mismo código
`MZ-BAR-<hash>` (identification.target_code), así que los ids coinciden con
los `identificacion.json` de los exports de Street View. Las agrupaciones
inválidas (más de 300 lotes, contorno no único o perímetro > 2 km) se separan
en lotes sueltos, como en barranco-studio. No modifica el GIS de origen.

Uso (desde web/, con el venv de barranco-streetview, que tiene shapely y pyproj):
    ..\\..\\barranco-streetview\\.venv\\Scripts\\python.exe scripts/build_manzanas.py ^
        ..\\..\\barranco-streetview\\data\\lotes.geojson
"""
import argparse
import hashlib
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree


def target_code(scope, lot_ids):
    ids = sorted(set(str(v) for v in lot_ids))
    digest = hashlib.sha256(json.dumps(['barranco-gis-v1', scope, ids], separators=(',', ':')).encode()).hexdigest()[:10].upper()
    return ('MZ' if scope == 'block' else 'LT') + '-BAR-' + digest


def short_name(code):
    """Nombre de escena propuesto: `mz_` + 6 primeros hex del código."""
    return 'mz_' + code.rsplit('-', 1)[1][:6].lower()


def rounded(geometry, digits=7):
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(v, digits) for v in value]
            return [walk(v) for v in value]
        return value
    data = mapping(geometry)
    return {'type': data['type'], 'coordinates': walk(data['coordinates'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, help='GeoJSON de lotes en WGS84 (propiedad lot_id)')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'public' / 'gis')
    args = parser.parse_args()

    raw = args.source.read_bytes()
    features = json.loads(raw)['features']
    to_m = Transformer.from_crs(4326, 32718, always_xy=True)
    to_geo = Transformer.from_crs(32718, 4326, always_xy=True)
    geoms = [transform(to_m.transform, shape(f['geometry'])) for f in features]
    tree = STRtree(geoms)

    seen, manzanas, lotes = set(), [], []
    for start in range(len(features)):
        if start in seen:
            continue
        group, pending = {start}, [start]
        while pending:
            for index in tree.query(geoms[pending.pop()], predicate='dwithin', distance=.1):
                index = int(index)
                if index not in group:
                    group.add(index)
                    pending.append(index)
        seen.update(group)
        union = unary_union([geoms[i] for i in group])
        valid = len(group) <= 300 and union.geom_type == 'Polygon' and union.length <= 2000
        for members in ([group] if valid else [{i} for i in sorted(group)]):
            ids = sorted(str(features[i]['properties']['lot_id']) for i in members)
            code = target_code('block', ids)
            geom = union if valid else geoms[next(iter(members))]
            lon, lat = to_geo.transform(*geom.representative_point().coords[0])
            manzanas.append({'type': 'Feature', 'geometry': rounded(transform(to_geo.transform, geom.simplify(.05))),
                             'properties': {'id': code, 'scene': short_name(code), 'lots': ids, 'lotCount': len(ids),
                                            'area_m2': round(geom.area, 1), 'perimeter_m': round(geom.length, 1),
                                            'center': [round(lat, 7), round(lon, 7)], 'groupingValid': valid}})
            for i in members:
                lotes.append({'type': 'Feature', 'geometry': rounded(shape(features[i]['geometry'])),
                              'properties': {'lotId': str(features[i]['properties']['lot_id']), 'manzana': code}})

    scenes = [m['properties']['scene'] for m in manzanas]
    if len(set(scenes)) != len(scenes):
        raise SystemExit('Colisión de nombres de escena cortos; ampliar el prefijo en short_name().')

    meta = {'sourceSha256': hashlib.sha256(raw).hexdigest(), 'source': args.source.name,
            'note': 'Agrupaciones geométricas de lotes del GIS; no son códigos catastrales.'}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, items in (('manzanas', manzanas), ('lotes', lotes)):
        (args.output / f'{name}.geojson').write_text(
            json.dumps({'type': 'FeatureCollection', 'meta': meta, 'features': items}, separators=(',', ':')),
            encoding='utf-8')
    print(f'{len(lotes)} lotes, {len(manzanas)} manzanas ({sum(m["properties"]["groupingValid"] for m in manzanas)} agrupadas) -> {args.output}')


if __name__ == '__main__':
    main()
