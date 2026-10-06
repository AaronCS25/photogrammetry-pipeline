# photogrammetry-studio (interfaz web local)

App **local** (`127.0.0.1:4323`) para operar el pipeline en Khipu sobre un
mapa de las manzanas de Barranco. Plan completo:
[`../docs/plan-interfaz-web.md`](../docs/plan-interfaz-web.md).

- **Ver** (fase 1): escenas de `datasets/raw/`, experimentos de `outputs/`
  (etapas, métricas, georef, máscaras, textura) y jobs `photogram` de las
  últimas 3 semanas, con log completo o filtrado.
- **Operar** (fase 2): subir capturas, crear experimentos (formulario simple o
  YAML), validar, lanzar, seguir, relanzar y cancelar.

Nunca ejecuta git ni toca `configs/` del repo: los YAML de la app viven en
`datasets/raw/<escena>/_ui/` (ignorada por el pipeline por el prefijo `_`).

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

## Uso

1. Elegir la manzana en el mapa → **Nueva captura**. Una fila por carpeta local
   (dron, teléfono, video, export de Street View). **Analizar** cuenta archivos,
   dimensiones, GPS y orientación; las fotos verticales se separan solas en
   `<fuente>_<ancho>x<alto>/`. **Subir a Khipu** sigue en segundo plano con
   progreso; si se corta, **Reanudar subida** solo envía lo que falta, y al final
   se verifica nombre y tamaño de cada archivo.
2. **Nuevo experimento**: preset (dron, teléfono, dron+teléfono, video, Street
   View) y conmutadores de máscaras, georef/ROI, calidad y recursos; o la
   pestaña **Avanzado** para editar el YAML. **Validar** sube el YAML y ejecuta
   `pipeline validate` en el maestro; **Lanzar** se habilita solo con la
   validación del YAML actual. Avisa si la RAM pedida más la de tus jobs activos
   supera los 130G del QOS.
3. El job aparece en la manzana (en cola / corriendo con la etapa actual).
   **Cancelar job** hace `scancel`. Al terminar: **Relanzar…** (reanudar o
   repetir desde una etapa) o **Nueva versión** (clona la configuración y puede
   reutilizar etapas copiando la carpeta del experimento).

Si se pierde la respuesta de un envío, aparece «envío incierto»: **Comprobar
envío** pregunta a Khipu con el mismo id (recibo en `_ui/requests/`); nunca se
relanza a ciegas.

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
- **Subidas**: un tar generado en Node por `ssh khipu "tar -x"` (sin rsync ni
  scp), una conexión por fuente, en un carril aparte para no bloquear las
  consultas. El EXIF queda intacto.
- **Estado local**: `data/studio.db` (SQLite nativo de Node, `node:sqlite`):
  último listado, nombres de manzanas, vínculos manuales, capturas, borradores
  de experimento, envíos y actividad. Khipu es la fuente de verdad de archivos
  y jobs.
  `STUDIO_DATA_DIR` permite otra ubicación.
- La API solo atiende loopback y comprueba el `Origin` en los POST. No es
  multiusuario: no exponerla en la red.
