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

## Publicación protegida

Un cambio en `main` no publica una versión. La publicación solo se permite mediante una ejecución manual del flujo, escribiendo la confirmación `PUBLICAR`. Antes de hacerlo deben revisarse el artefacto de Windows, las pruebas y las capturas.

`latest.json` y las sumas oficiales solo se actualizan durante una publicación manual confirmada.
