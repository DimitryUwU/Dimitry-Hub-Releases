# Capturas de Dimitry Hub

Las imágenes de esta carpeta documentan estados observados durante las pruebas y no sustituyen los resultados automatizados de GitHub Actions.

## Sobre `actualizaciones.png`

Esta captura se tomó en un entorno de pruebas que impedía conexiones salientes. El mensaje `WinError 10013` indica que Windows o la red bloquearon el acceso HTTPS; no representa el estado normal ni un error permanente de Dimitry Hub.

En una instalación con acceso a Internet, **Sincronizar datos** vuelve a consultar las noticias oficiales de Palworld y la versión de Palworld Save Pal. Si una fuente falla, Dimitry Hub muestra el motivo y conserva el último conjunto válido, sin borrar datos ni modificar guardados.

Si el mismo código aparece en un equipo personal, debe permitirse la conexión saliente HTTPS de `DimitryHub.exe` en Windows Defender, el antivirus, el proxy o el cortafuegos de la red.

La versión 1.1.1 fue validada en Windows por GitHub Actions, incluida la compilación, instalación, apertura, comprobación de salud, carga de la pantalla inicial y desinstalación.
