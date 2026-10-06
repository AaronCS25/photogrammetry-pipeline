// Lógica pura (sin E/S): vínculo escena→manzana y estados derivados del listado de Khipu.

export const ACTIVE = new Set(['PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED']);
// Orden de prioridad para colorear una manzana: el primero presente gana.
export const PRIORITY = ['running', 'queued', 'done', 'failed', 'partial', 'data', 'none'];

function inRing([x, y], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
export function contains(geometry, point) {
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
  return polygons.some(([outer, ...holes]) => inRing(point, outer) && !holes.some(h => inRing(point, h)));
}

// Distancia aproximada en metros de un punto a un polígono (equirectangular local; basta a escala de distrito).
function distanceM(geometry, [lon, lat]) {
  if (contains(geometry, [lon, lat])) return 0;
  const kx = 111320 * Math.cos(lat * Math.PI / 180), ky = 110540;
  const rings = geometry.type === 'Polygon' ? geometry.coordinates : geometry.coordinates.flat();
  let best = Infinity;
  for (const ring of rings) for (let i = 1; i < ring.length; i++) {
    const ax = (ring[i - 1][0] - lon) * kx, ay = (ring[i - 1][1] - lat) * ky, bx = (ring[i][0] - lon) * kx, by = (ring[i][1] - lat) * ky;
    const dx = bx - ax, dy = by - ay, t = Math.max(0, Math.min(1, -(ax * dx + ay * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(ax + t * dx, ay + t * dy));
  }
  return best;
}

/** Índice de manzanas: por id, por nombre corto de escena y búsqueda espacial lineal (311 polígonos). */
export function indexManzanas(collection) {
  const byId = new Map(), byScene = new Map();
  for (const f of collection.features) { byId.set(f.properties.id, f); byScene.set(f.properties.scene, f); }
  return {
    byId, byScene,
    // Las cámaras (dron, panorama) suelen estar sobre la calle: manzana que contiene el punto o la más cercana a ≤ maxM.
    near(lat, lon, maxM = 30) {
      let best = null, bestD = maxM;
      for (const f of collection.features) { const d = distanceM(f.geometry, [lon, lat]); if (d <= bestD) { best = f.properties.id; bestD = d; } }
      return best;
    },
  };
}

/**
 * Manzana de una escena, por orden de confianza:
 * manual (BD local) > identificacion.json del export de Street View > nombre `mz_xxxxxx` > origen GPS del georef.
 */
export function linkScene(scene, index, manual) {
  if (manual && Object.hasOwn(manual, scene.name)) return { manzana: manual[scene.name], via: 'manual' };
  const code = scene.identification?.code;
  if (code && index.byId.has(code)) return { manzana: code, via: 'export' };
  const prefix = scene.name.match(/^(mz_[0-9a-f]{6})/i)?.[1].toLowerCase();
  if (prefix && index.byScene.has(prefix)) return { manzana: index.byScene.get(prefix).properties.id, via: 'nombre' };
  for (const exp of scene.experiments || []) {
    const o = exp.origin;
    if (o && Number.isFinite(o.lat) && Number.isFinite(o.lon) && !(o.lat === 0 && o.lon === 0)) {
      const id = index.near(o.lat, o.lon);
      if (id) return { manzana: id, via: 'georef' };
    }
  }
  return { manzana: null, via: null };
}

/** Jobs de un experimento: los de los marcadores .stages más los que el log asocia a escena+experimento. */
export function experimentJobs(scene, exp, jobs) {
  const ids = new Set(exp.jobs || []);
  if (exp.metrics?.job) ids.add(String(exp.metrics.job));
  return jobs.filter(j => ids.has(j.id) || (j.scene === scene && j.experiment === exp.name)).sort((a, b) => b.id - a.id);
}

export function experimentStatus(exp, jobs) {
  const active = jobs.find(j => ACTIVE.has(j.state));
  if (active) return { status: active.state === 'PENDING' ? 'queued' : 'running', job: active };
  const last = jobs[0];
  if (last && last.state && last.state !== 'COMPLETED') return { status: 'failed', job: last };
  if (exp.textured || exp.stages?.metrics) return { status: 'done', job: last || null };
  return { status: 'partial', job: last || null };
}

/** Enriquece el listado remoto: manzana de cada escena, estado de cada experimento y estado por manzana. */
export function derive(snapshot, index, manual = {}) {
  const jobs = snapshot?.jobs || [];
  const scenes = (snapshot?.scenes || []).map(scene => {
    const experiments = (scene.experiments || []).map(exp => {
      const own = experimentJobs(scene.name, exp, jobs);
      return { ...exp, jobs: own.map(j => j.id), ...experimentStatus(exp, own) };
    });
    // Jobs activos que aún no crearon carpeta de experimento (p. ej. en cola).
    const known = new Set(experiments.flatMap(e => e.jobs));
    const pending = jobs.filter(j => j.scene === scene.name && ACTIVE.has(j.state) && !known.has(j.id));
    for (const j of pending) experiments.unshift({ name: j.experiment || '(sin carpeta aún)', stages: {}, jobs: [j.id], status: j.state === 'PENDING' ? 'queued' : 'running', job: j, placeholder: true });
    const status = PRIORITY.find(s => experiments.some(e => e.status === s)) || (scene.sources?.length ? 'data' : 'none');
    return { ...scene, experiments, status, ...linkScene(scene, index, manual) };
  });
  const manzanas = {};
  for (const s of scenes) {
    if (!s.manzana) continue;
    const m = manzanas[s.manzana] ||= { scenes: [], status: 'none' };
    m.scenes.push(s.name);
    if (PRIORITY.indexOf(s.status) < PRIORITY.indexOf(m.status)) m.status = s.status;
  }
  return { scenes, manzanas, jobs };
}
