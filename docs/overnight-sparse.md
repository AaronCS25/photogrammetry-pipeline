# Cola nocturna de manzanas sparse

La cuota consultada en Khipu para `a-tesis` tiene `MaxJobsPU=3` y
`MaxSubmitJobsPU=5`: como máximo cinco jobs enviados (pendientes + activos).
Una reconstrucción sparse del pipeline tiene cinco fases/jobs. Por tanto no
se debe invocar `submit-batch` con muchas manzanas bajo esta cuota: Slurm no
aceptaría todas sus cadenas simultáneamente.

`tools/sparse_queue.py` es un coordinador ligero para ejecutar dentro de tmux
en el login. Solo hace inventario liviano, consulta estados y envía jobs. El
cómputo siempre corre por Slurm/Apptainer. Espera cinco huecos, envía una sola
manzana y avanza a la siguiente cuando termina. No cancela jobs ajenos; si una
fase falla, cancela solo los descendientes de esa solicitud para liberar cuota
y poder continuar con las siguientes manzanas. Un envío incierto pausa la cola.

## Selección de esta campaña

- Ya terminada: **001** (244/244 imágenes, 230851 puntos).
- En cola automática: **002,003,004,005,007,008,009,012,013,017,018**.
- Excluidas por MP4 HTML: **023,024,025** (se excluye toda la carpeta).
- Los otros IDs no tienen videos locales disponibles.

Cada escena se inventaría antes del arranque y nuevamente antes de enviarla.
Si sus datos, configuración o fuentes Python cambian mientras espera, se
marca bloqueada en vez de procesar otro plan silenciosamente. No se debe
modificar el pipeline durante la campaña sin revisar/recrear su cola.

## Operación

El manifiesto es `datasets/_state/batches/sparse-validas-20261028.json`.
El log es `slurm-logs/sparse-validas-20261028.log`.
La sesión usa el servidor tmux `sparse-validas` y sesión `sparse-validas`:

```bash
tmux -L sparse-validas attach -t sparse-validas
# Ctrl+B, D para salir sin detener el coordinador.
```

La cola de Slurm mostrará solo la manzana actual; las demás constan como
`waiting` en el manifiesto y se enviarán automáticamente. Consultar con
`pipeline status --scene <ID> --live` una vez que una escena haya sido enviada.

El coordinador puede reanudarse ejecutando el mismo script con `--manifest`
(sin `--initialize`); recupera los recibos existentes antes de enviar y tiene un
bloqueo para no ejecutar dos instancias sobre el mismo manifiesto.

El horario final depende de la espera del cluster y de la dificultad de cada
manzana. Los tiempos de matching crecen con los pares de imágenes; el tiempo
de una manzana de 651 frames no será igual al de la 001 con 244 frames.
