# Dimitry Hub

Dimitry Hub es una aplicación local para Windows con servidor Python e interfaz web. El código fuente reproducible y legible vive en `src/`.

## Desarrollo local

1. Instala Python 3.12.
2. Instala las dependencias con `python -m pip install -r src/requirements.txt`.
3. Ejecuta `python src/run.py`.
4. Abre `http://127.0.0.1:8765`.

## Pruebas

Desde `src/` ejecuta:

```text
python -m compileall app run.py updater.py
python -m unittest discover -s tests -p "test_*.py"
node --check app/static/app.js
```

El flujo de GitHub Actions valida los pull requests, compila los ejecutables, genera un instalador de prueba y conserva el resultado como artefacto.

## Parejas perfectas de Palworld

En una sesión del Editor Palworld, el botón **Preparar parejas perfectas** sincroniza el catálogo compatible, clasifica cada especie por función y genera perfiles importables para machos, hembras y especies nuevas. Los perfiles mantienen el nivel 1 y la aptitud de trabajo nativa; llevan cuatro pasivas elegidas por función, IV, almas y rango máximos. La aplicación conserva el respaldo y deja la escritura final de la partida guardada al editor compatible.

## Uso sencillo e IA local

La interfaz utiliza textos guiados en español, mensajes de conexión comprensibles y selectores de archivos propios. Los sonidos y las animaciones pueden activarse o desactivarse en **Ajustes > Respuesta de la interfaz**. Ollama puede configurarse como proveedor local; cuando el razonamiento está desactivado, Dimitry Hub oculta cualquier bloque interno y muestra únicamente la respuesta final.

Los tres estilos cambian la experiencia completa, no solo los colores. **Despacho profesional** aplica tipografía académica, formas sobrias, movimientos mínimos y sonidos discretos; **Realeza arcana** usa controles ornamentales, profundidad suave y campanillas; **Aventura nocturna** adopta una respuesta más dinámica, brillos y sonidos enérgicos. Al elegir un estilo también se aplica su tipografía recomendada, que luego puede personalizarse. Las preferencias de sonidos, movimiento reducido y accesibilidad se respetan en los tres.

## Publicación protegida

Un cambio en `main` no publica una versión. La publicación solo se permite mediante una ejecución manual del flujo, escribiendo la confirmación `PUBLICAR`. Antes de hacerlo deben revisarse el artefacto de Windows, las pruebas y las capturas.

`latest.json` y las sumas oficiales solo se actualizan durante una publicación manual confirmada.
