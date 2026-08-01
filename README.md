# Dimitry Hub — canal oficial de versiones

Este repositorio publica el instalador oficial de **Dimitry Hub para Windows** y los archivos que permiten que la aplicación se mantenga actualizada.

## Cómo funciona

- Se instala una sola vez con `Dimitry_Hub_Setup_x64.exe`.
- Al abrirse, Dimitry Hub consulta `latest.json`.
- Cuando existe una versión más reciente, descarga el instalador oficial, verifica su huella SHA-256 y realiza la actualización.
- Los proyectos, documentos y ajustes personales se guardan fuera de la carpeta del programa, en `%LOCALAPPDATA%\Dimitry Hub\Data`, para conservarlos durante las actualizaciones o reinstalaciones.

## Archivos publicados

- `Dimitry_Hub_Setup_x64.exe`: instalador completo para una instalación nueva o una actualización.
- `latest.json`: manifiesto usado por la actualización automática.
- `checksums.sha256`: huella de integridad del instalador.

Las versiones terminadas se encuentran en la sección **Releases** del repositorio.
