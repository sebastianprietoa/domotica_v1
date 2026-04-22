# Architecture

## Capas principales

- `config`: carga `.env`, parsea `config.yaml` y valida parametros.
- `screen_capture`: captura frames desde el monitor configurado usando `mss`.
- `color_extractor`: divide la imagen en zonas y calcula color promedio o dominante.
- `smoothing`: aplica threshold, blending temporal y limitacion de frecuencia.
- `device_mapper`: resuelve que dispositivos deben recibir el color de cada zona.
- `tuya_client`: encapsula `TuyaOpenAPI` para autenticacion, lectura y comandos.
- `sync_engine`: loop principal de captura -> analisis -> smoothing -> envio.

## Reutilizacion del proyecto base

El SDK `tuya_connector/` queda intacto y actua como adaptador cloud de bajo nivel.

## Flujo del modo sync

1. Se carga `.env` y `config/config.yaml`.
2. Se autentica el cliente Tuya.
3. Se captura un frame del monitor principal.
4. Se divide el frame en zonas configuradas.
5. Se calcula color promedio o dominante por zona.
6. Se aplica smoothing y suppression de cambios pequenos.
7. Se mapea cada zona a uno o varios dispositivos.
8. Se envian comandos Tuya solo cuando el cambio supera los limites configurados.
