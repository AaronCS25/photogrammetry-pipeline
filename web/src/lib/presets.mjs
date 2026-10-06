// Presets del formulario "simple" → YAML de experimento (se combina con configs/default.yaml).
// Lógica pura, compartida por el servidor y el navegador. Los valores base vienen de los
// experimentos ya probados en configs/experiments/ (citados en cada preset).
import { stringify } from 'yaml';

export const STAGES = ['frames', 'telemetry', 'masks', 'sfm', 'georef', 'undistort', 'dense', 'mesh', 'texture', 'metrics'];
export const QOS_MEM_G = 130; // límite de RAM sumando todos los jobs del usuario (QOS a-investigacion1)

const SEGFORMER_CLASSES = ['person', 'rider', 'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'sky'];
const CABLES = ['power line', 'electric cable', 'wire'];

export const PRESETS = {
  dron: {
    label: 'Dron (fotos)', source: 'hornero_drone_geo_tiles.yaml',
    hint: 'Fotos de dron con GPS: SIFT afín, matching exhaustivo, SAM 3 para objetos y cables, georef.',
    form: { masking: { enabled: true, segformer: false, sam3: true, manual: false, classes: SEGFORMER_CLASSES.join(', '),
      prompts: 'car, truck, bus, motorcycle, person, tree, utility pole, lamp post, power line', cableTiles: true }, georef: { enabled: true } },
    base: {
      frames: { resize: null }, telemetry: { enabled: false },
      colmap: { camera_model: 'OPENCV',
        feature_extractor: { max_num_features: 16384, extra_args: { 'SiftExtraction.estimate_affine_shape': 1, 'SiftExtraction.domain_size_pooling': 1, 'SiftExtraction.num_threads': 8 } },
        matcher: { methods: ['exhaustive'], extra_args: { 'SiftMatching.guided_matching': 1 } },
        mapper: { extra_args: { 'Mapper.abs_pose_min_num_inliers': 20, 'Mapper.min_num_matches': 10 } } },
      openmvs: { mesh: { extra_args: { 'remove-spurious': 40 } }, texture: { extra_args: { 'empty-color': 8421504 } } },
    },
  },
  telefono: {
    label: 'Teléfono (fotos)', source: 'barranco_phone_photos.yaml',
    hint: 'Fotos de teléfono con GPS en EXIF, sin reescalar: matching secuencial + espacial.',
    form: { masking: { enabled: true, segformer: true, sam3: false, manual: false, classes: SEGFORMER_CLASSES.join(', '),
      prompts: 'power line, electric cable, wire, utility pole', cableTiles: false }, georef: { enabled: true } },
    base: {
      frames: { resize: null }, telemetry: { enabled: false },
      colmap: { camera_model: 'OPENCV', feature_extractor: { max_num_features: 8192 },
        matcher: { methods: ['sequential', 'spatial'], sequential_overlap: 15 } },
      openmvs: { texture: { extra_args: { 'empty-color': 8421504 } } },
    },
  },
  dron_telefono: {
    label: 'Dron + teléfono', source: 'barranco_full_masked_sam3.yaml',
    hint: 'Fuentes drone/ y phone/: secuencial + vocab tree para unirlas; el dron usa solo SAM 3 (SegFormer alucina en vista aérea).',
    form: { masking: { enabled: true, segformer: true, sam3: true, manual: false,
      classes: [...SEGFORMER_CLASSES, 'vegetation', 'pole', 'traffic_light', 'traffic_sign'].join(', '),
      prompts: 'power line, electric cable, wire, utility pole, lamp post', cableTiles: false }, georef: { enabled: true } },
    base: {
      frames: { resize: null }, telemetry: { enabled: false },
      colmap: { camera_model: 'OPENCV',
        matcher: { methods: ['sequential', 'vocab_tree'], sequential_overlap: 15, vocab_tree_path: 'resources/vocab_tree_flickr100K_words256K.bin' } },
      openmvs: { mesh: { decimate: 0.7, extra_args: { 'remove-spurious': 40 } }, texture: { extra_args: { 'empty-color': 8421504 } } },
    },
  },
  video: {
    label: 'Video', source: 'configs/default.yaml',
    hint: 'Video (dron DJI con .srt o teléfono): frames con ffmpeg y matching secuencial.',
    form: { masking: { enabled: false, segformer: true, sam3: false, manual: false, classes: SEGFORMER_CLASSES.join(', '),
      prompts: 'power line, electric cable, wire', cableTiles: false }, georef: { enabled: false }, video: { fps: 2, longEdge: 1920 } },
    base: { telemetry: { enabled: true }, colmap: { camera_model: 'OPENCV', matcher: { methods: ['sequential'] } } },
  },
  streetview: {
    label: 'Street View (export)', source: 'streetview_manzana.yaml',
    hint: 'Export de barranco-streetview subido tal cual: PINHOLE de camaras.json, franja de atribución, georef con indice.csv. Caso negativo documentado (panoramas cada ~10 m).',
    form: { masking: { enabled: true, segformer: true, sam3: false, manual: true,
      classes: [...SEGFORMER_CLASSES, 'vegetation', 'pole'].join(', '), prompts: 'power line, electric cable, wire', cableTiles: false }, georef: { enabled: true } },
    base: {
      frames: { resize: null }, telemetry: { enabled: false },
      colmap: { camera_model: 'PINHOLE',
        feature_extractor: { max_num_features: 16384, extra_args: { 'ImageReader.camera_params': '953.4028740753681,953.4028740753681,800,600',
          'SiftExtraction.estimate_affine_shape': 1, 'SiftExtraction.domain_size_pooling': 1, 'SiftExtraction.num_threads': 8 } },
        matcher: { methods: ['exhaustive'], extra_args: { 'SiftMatching.guided_matching': 1 } },
        mapper: { extra_args: { 'Mapper.ba_refine_focal_length': 0, 'Mapper.ba_refine_extra_params': 0, 'Mapper.abs_pose_min_num_inliers': 20, 'Mapper.min_num_matches': 10 } } },
      georef: { reference_file: 'indice.csv', max_error_m: 5.0, ground_below_min_camera_m: 2.5 },
      openmvs: { texture: { extra_args: { 'empty-color': 8421504 } } },
    },
  },
};

// Calidad → tamaño máximo para SIFT/undistort, nivel de resolución del denso y refinado.
export const QUALITY = {
  rapida: { label: 'Rápida', maxImage: 1600, resolution: 2, refine: null },
  normal: { label: 'Normal', maxImage: 2400, resolution: 1, refine: 1 },
  maxima: { label: 'Máxima', maxImage: 3200, resolution: 1, refine: 2 },
};
export const GPUS = { rtxa6000: 'RTX A6000 48 GB', a100: 'A100 40 GB', tesla: 'Tesla T4 16 GB (sin SAM 3)' };

const clone = v => JSON.parse(JSON.stringify(v));
const list = text => String(text || '').split(',').map(s => s.trim()).filter(Boolean);
const num = (v, fallback = null) => (v === '' || v == null || !Number.isFinite(Number(v))) ? fallback : Number(v);

export function defaultForm(preset = 'telefono', name = '') {
  const p = PRESETS[preset];
  return {
    preset, name: name || `${preset}_v1`, notes: '',
    masking: clone(p.form.masking),
    georef: { enabled: p.form.georef.enabled, radius_m: null, height_m: null },
    quality: 'normal',
    video: clone(p.form.video || { fps: 2, longEdge: 1920 }),
    resources: { gpu: 'rtxa6000', shards: 8, cpus: 16, mem: 64, hours: 6 },
  };
}

export function needsSam3(form) { return Boolean(form.masking?.enabled && form.masking.sam3); }

/** Objeto de configuración (override sobre default.yaml) a partir del formulario simple. */
export function buildConfig(form) {
  const p = PRESETS[form.preset];
  if (!p) throw Error('Preset desconocido.');
  const cfg = clone(p.base);
  cfg.experiment = { name: form.name, notes: form.notes || `${p.label} (generado por photogrammetry-studio)` };
  const q = QUALITY[form.quality] || QUALITY.normal;
  const col = cfg.colmap ||= {};
  const small = form.preset === 'streetview'; // imágenes de 1600 px: no reescalar ni bajar resolución
  (col.feature_extractor ||= {}).max_image_size = small ? 1600 : q.maxImage;
  if (!small) col.undistort = { max_image_size: q.maxImage };
  const mvs = cfg.openmvs ||= {};
  mvs.densify = { ...(mvs.densify || {}), resolution_level: small ? 0 : q.resolution };
  mvs.refine = q.refine ? { enabled: true, scales: q.refine } : { enabled: false };
  if (form.preset === 'video') cfg.frames = { fps: num(form.video?.fps, 2), resize: num(form.video?.longEdge) ? { long_edge: num(form.video.longEdge) } : null };

  const m = form.masking || {};
  const chain = ['segformer', 'sam3', 'manual'].filter(b => m[b]);
  if (m.enabled && chain.length) {
    const masking = { enabled: true, backend: chain, dilate_px: 15, apply_to_dense: true, apply_to_texture: true, backends: {} };
    if (m.segformer) masking.classes = list(m.classes);
    if (m.sam3) {
      masking.backends.sam3 = { prompts: list(m.prompts), score_threshold: 0.5 };
      if (m.cableTiles) Object.assign(masking.backends.sam3, { tiles: [6, 4], tile_overlap: 0.15, tile_prompts: CABLES });
    }
    if (m.manual) masking.backends.manual = { dir: form.preset === 'streetview' ? 'mascaras' : 'mask_overrides' };
    if (form.preset === 'dron_telefono' && m.sam3) masking.per_source = { drone: { backend: ['sam3'] } };
    if (!Object.keys(masking.backends).length) delete masking.backends;
    cfg.masking = masking;
  } else cfg.masking = { enabled: false };

  const g = form.georef || {};
  if (g.enabled) {
    const radius = num(g.radius_m);
    cfg.georef = { ...(cfg.georef || {}), enabled: true, alignment_type: 'enu',
      roi: radius ? { enabled: true, radius_m: radius, below_m: 3.0, ...(num(g.height_m) ? { height_m: num(g.height_m) } : {}) } : { enabled: false } };
  } else cfg.georef = { enabled: false };
  return cfg;
}

export function toYaml(cfg, header = '') {
  return (header ? header.split('\n').map(l => `# ${l}`).join('\n') + '\n' : '') + stringify(cfg, { lineWidth: 0 });
}

export function sbatchFromForm(form) {
  const r = form.resources || {};
  const gpu = needsSam3(form) && r.gpu === 'tesla' ? 'rtxa6000' : r.gpu || 'rtxa6000';
  return `--gres=shard:${gpu}:${num(r.shards, 8)} --cpus-per-task=${num(r.cpus, 16)} --mem=${num(r.mem, 64)}G --time=0-${num(r.hours, 6)}:00:00`;
}

// Solo se aceptan estas opciones de sbatch (se pasan por SBATCH_OPTS a slurm/submit.sh).
const ALLOWED = [
  /^--gres=(shard:(tesla|rtxa6000|a100):\d{1,2}|shard:\d{1,2}|gpu:(tesla:|rtxa6000:|a100:)?1)$/,
  /^--cpus-per-task=\d{1,3}$/, /^--mem=\d{1,4}[GM]$/, /^--time=(\d{1,2}-)?\d{1,2}:\d{2}:\d{2}$/,
  /^--partition=[a-z-]{1,20}$/, /^--(nodelist|exclude)=[a-z0-9,]{1,60}$/,
];
export function parseSbatch(text) {
  const tokens = String(text || '').trim().split(/\s+/).filter(Boolean);
  for (const t of tokens) if (!ALLOWED.some(r => r.test(t))) throw Error(`Opción de sbatch no permitida: ${t}`);
  if (tokens.length > 12) throw Error('Demasiadas opciones de sbatch.');
  return tokens;
}
export function memG(text) {
  const m = String(text || '').match(/--mem=(\d+)([GM])/);
  return m ? (m[2] === 'G' ? Number(m[1]) : Number(m[1]) / 1024) : 64; // 64G es el default de pipeline.sbatch
}
export function gresType(text) { return String(text || '').match(/--gres=(?:shard|gpu):(tesla|rtxa6000|a100):/)?.[1] || null; }

/** Siguiente nombre libre `<prefijo>_v<N>` dados los existentes. */
export function nextName(prefix, existing) {
  let n = 0;
  for (const name of existing) { const m = name.match(new RegExp(`^${prefix}_v(\\d+)`)); if (m) n = Math.max(n, Number(m[1])); }
  return `${prefix}_v${n + 1}`;
}
