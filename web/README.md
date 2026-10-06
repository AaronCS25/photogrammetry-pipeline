# photogrammetry-studio (interfaz web local)

App **local** (`127.0.0.1:4323`) para ver y, en fases siguientes, operar el
pipeline en Khipu sobre un mapa de las manzanas de Barranco. Plan completo:
[`../docs/plan-interfaz-web.md`](../docs/plan-interfaz-web.md).

**Fase 1 (actual): solo lectura.** Lista escenas de `datasets/raw/`,
experimentos de `outputs/` (etapas hechas, métricas, georef, máscaras,
textura) y los jobs `photogram` de las últimas 3 semanas (`sacct` + `squeue`),
con log completo o filtrado bajo demanda. No escribe nada en Khipu, no lanza
jobs y no ejecuta git.

## Arranque (Windows)

Requiere Node >= 22.13, OpenSSH (`ssh`) y el alias `khipu` en `~/.ssh/config`
con autenticación por clave (la app usa `BatchMode=yes`; nunca pide ni guarda
contraseñas).

```powershell
cd photogrammetry-pipeline\web
npm ci          # la primera vez
npm run build
npm start       # http://127.0.0.1:4323
```

`npm run dev` sirve la versión de desarrollo. `npm test` corre los tests de Node
(vínculos, estados, BD) y de Python (bridge remoto contra un árbol falso).

## Cómo funciona

- **Mapa**: `public/gis/manzanas.geojson` (311 manzanas) y `lotes.geojson`
  (capa opcional), precomputados una vez desde el GIS de `barranco-streetview`:

  ```powershell
  ..\..\barranco-streetview\.venv\Scripts\python.exe scripts\build_manzanas.py ..\..\barranco-streetview\data\lotes.geojson
  ```

  Los ids `MZ-BAR-<hash>` son los mismos que usan barranco-streetview y
  barranco-studio. Nombre de escena propuesto por manzana: `mz_<6 hex>`.
- **Khipu**: un solo `ssh khipu python3 -` por consulta; el script
  `scripts/remote_bridge.py` viaja por stdin (patrón de
  `sam3d-barranco/web/src/lib/connector.mjs`). Consultas serializadas y como
  máximo un listado cada 30 s. Seguimiento automático cada 5 min solo si hay
  jobs activos y la pestaña está visible.
- **Escena → manzana**, por orden: vínculo manual (guardado localmente) >
  `identificacion.json` o `manifest.json` del export de Street View > nombre
  `mz_xxxxxx…` > origen GPS del georef (manzana que lo contiene o la más
  cercana a ≤ 30 m; aproximado). Las escenas sin manzana aparecen en el panel
  inicial y se asignan con un clic en el mapa.
- **Estado local**: `data/studio.db` (SQLite nativo de Node, `node:sqlite`):
  último listado, nombres libres de manzanas y vínculos manuales. Khipu es la
  fuente de verdad; borrar `data/` solo pierde nombres y vínculos manuales.
  `STUDIO_DATA_DIR` permite otra ubicación.
- La API solo atiende loopback y comprueba el `Origin` en los POST. No es
  multiusuario: no exponerla en la red.
