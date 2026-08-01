# Dimitry Hub — canal oficial de versiones

Este repositorio publica el instalador oficial de Dimitry Hub para Windows y los archivos necesarios para mantener la aplicación actualizada.

## Funcionamiento

- Se instala una sola vez con `Dimitry_Hub_Setup_x64.exe`.
- Al abrirse, Dimitry Hub consulta `latest.json`.
- Si existe una versión más reciente, descarga el instalador oficial y verifica su SHA-256.
- Los proyectos, documentos y ajustes personales se conservan en `%LOCALAPPDATA%\Dimitry Hub\Data`.
- El mismo instalador sirve para una instalación nueva o para actualizar una existente.

## Publicación

GitHub Actions compila el programa en Windows, genera el instalador, crea la publicación de GitHub y actualiza automáticamente:

- `Dimitry_Hub_Setup_x64.exe`
- `latest.json`
- `checksums.sha256`

El código fuente verificado de la versión 1.0.0 se almacena en:

`source/Dimitry_Hub_Source_v1.0.0.zip`
