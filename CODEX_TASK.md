# Tarea de Codex: estabilizar y reconstruir Dimitry Hub

## Repositorio

`DimitryUwU/Dimitry-Hub-Releases`

## Contexto real del proyecto

Dimitry Hub es una aplicación local para Windows. El ejecutable inicia un servidor local y abre la interfaz en el navegador, normalmente en `127.0.0.1:8765`. La aplicación usa Python para el backend, HTML/CSS/JavaScript para la interfaz, PyInstaller para generar ejecutables e Inno Setup para producir `Dimitry_Hub_Setup_x64.exe`.

El repositorio actual no tiene una arquitectura limpia: conserva un ZIP base en `source/Dimitry_Hub_Source_v1.0.0.zip` y aplica varios parches durante GitHub Actions. El parche `patches/apply_v110.py` contiene un payload enorme comprimido en Base64/Gzip. Esta estrategia ya provocó regresiones difíciles de detectar y debe reemplazarse por código fuente normal, legible y verificable.

La versión publicada `1.1.0` está rota. Al abrirla se muestran la barra lateral y el encabezado, pero el área principal queda completamente vacía. Los botones del menú no cargan ninguna sección. La captura observada muestra Inicio seleccionado, el encabezado “Inicio” y todo el contenido central en blanco. Esto sugiere un error JavaScript en tiempo de ejecución o una incompatibilidad creada por la cadena de parches, aunque Codex debe reproducirlo y confirmar la causa exacta antes de modificar nada.

## Regla principal

No publiques, no etiquetes y no sustituyas la versión estable hasta que el producto funcione de extremo a extremo y todas las pruebas indicadas aquí pasen.

Trabaja en una rama nueva, por ejemplo:

`codex/stabilize-dimitry-hub`

Al terminar, abre un pull request contra `main` con diagnóstico, capturas, pruebas ejecutadas y riesgos pendientes. No hagas push directo de una versión rota a `main`.

---

# Objetivos obligatorios

## 1. Reproducir y corregir la pantalla vacía

Antes de implementar funciones nuevas:

1. Reconstruye exactamente el código que genera la versión 1.1.0 aplicando la cadena actual de parches.
2. Ejecuta la aplicación en un entorno de desarrollo.
3. Abre la interfaz con un navegador automatizado.
4. Captura errores de consola, errores de red, excepciones del backend y rutas que fallen.
5. Identifica la causa raíz de que el contenido central quede vacío y de que la navegación no responda.
6. Corrige la causa, no solo el síntoma.

Criterios mínimos:

- Inicio muestra contenido real, no un panel vacío.
- Dimitry AI abre su sección.
- Estudio abre su sección.
- Palworld Wiki abre su sección.
- Editor Palworld abre su sección.
- Game Lab abre su sección.
- Actualizaciones abre su sección.
- Ajustes abre su sección.
- Ningún clic normal genera errores en la consola.
- La interfaz muestra un estado de error visible si una API falla, en vez de quedarse en blanco.

## 2. Reestructurar el código fuente

Deja de construir la aplicación mediante payloads gigantes comprimidos o parches opacos.

Debes:

1. Materializar el código fuente final y legible en una carpeta normal del repositorio, preferiblemente `src/`.
2. Integrar dentro de ese árbol los cambios válidos de las versiones 1.0.1 a 1.1.0.
3. Eliminar la dependencia de `patches/apply_v110.py` y de cualquier payload Base64/Gzip para construir el producto.
4. Mantener scripts de migración solo cuando sean pequeños, legibles, idempotentes y probados.
5. Actualizar GitHub Actions para construir directamente desde el árbol fuente real.
6. Hacer que los pull requests solo ejecuten validaciones y generen artefactos de prueba.
7. Reservar la publicación de releases para un tag explícito o un `workflow_dispatch` con versión confirmada.

No debe volver a publicarse una release solo porque se modificó un archivo en `main`.

## 3. Separar Palworld Wiki y Editor Palworld

La navegación debe distinguir claramente dos productos diferentes.

Diseño requerido:

- `Palworld Wiki`: consulta, búsqueda, biblioteca y guías.
- `Editor Palworld`: importación, respaldo, edición y exportación de partidas.

La solución preferida es mostrar dos elementos separados en el menú izquierdo:

- Palworld Wiki
- Editor Palworld

También es aceptable que el botón Palworld despliegue un submenú con esas dos opciones, siempre que ambas sean accesibles directamente, permanezcan visibles y tengan rutas independientes.

No mezcles una biblioteca de conocimiento con un editor de saves en una sola pantalla confusa.

## 4. Palworld Wiki funcional sin proveedor de IA

La Wiki debe funcionar aunque el usuario no tenga OpenAI, Ollama ni otro proveedor configurado.

Implementa:

- búsqueda local por nombre de Pal, pasiva, habilidad, objeto, mecánica o tema;
- resultados claros y navegables;
- fichas legibles;
- estado vacío, estado cargando y estado de error;
- respuesta local basada en la base sincronizada;
- indicador claro cuando una respuesta avanzada requiere IA;
- ningún botón debe quedarse sin responder.

No inventes información cuando no existan datos. Muestra “No se encontró información suficiente” y ofrece términos relacionados.

## 5. Editor Palworld realmente utilizable

El Editor Palworld debe aceptar directamente:

- `Level.sav`
- `GlobalPalStorage.sav`
- archivos de jugador `.sav`
- un ZIP completo de la carpeta de guardado

Flujo obligatorio:

1. El usuario selecciona o arrastra el archivo.
2. La interfaz valida nombre, extensión, tamaño y estructura básica.
3. Se crea una sesión de trabajo identificable.
4. Se guarda una copia original inmutable.
5. Se crea una copia editable separada.
6. La interfaz muestra qué archivos fueron detectados.
7. El usuario puede abrir el editor correspondiente.
8. Puede descargar los archivos modificados.
9. Puede restaurar el original.
10. Puede eliminar la sesión con confirmación.

Los datos deben permanecer en `%LOCALAPPDATA%\Dimitry Hub\Data` y nunca dentro de la carpeta reemplazable del programa.

### Áreas del editor

La interfaz debe organizar las funciones, como mínimo, en estas categorías:

- Pals y Palbox
- Global Pal Storage
- Jugadores
- Inventario y equipo
- Tecnologías y progreso
- Misiones
- Gremios
- Mundo y mapa
- Copias de seguridad y exportación

Incluye accesos claros para tareas habituales del usuario:

- revisar y editar Pals;
- cambiar nombre, sexo, nivel, rango, estadísticas, pasivas y habilidades cuando el motor realmente lo soporte;
- crear parejas macho/hembra;
- trabajar con la Caja Pal global;
- editar inventario y progreso;
- exportar un save listo para reemplazar en el juego.

### Integración con editor externo

Inspecciona la integración existente con Palworld Save Pal y su repositorio upstream real.

- Verifica licencia, método de instalación, comandos y formato esperado.
- No descargues todo el repositorio en cada sincronización.
- Separa “comprobar versión” de “instalar editor”.
- Verifica hashes cuando descargues binarios o paquetes.
- Si el editor externo abre una interfaz propia, Dimitry Hub debe preparar la sesión correcta y abrirlo apuntando a la copia editable.
- Si una función no está soportada por el motor real, no muestres un botón que finja hacerla.

La aplicación debe distinguir entre:

- función disponible;
- función pendiente de instalar;
- función no compatible con ese tipo de save;
- error real con explicación y solución.

## 6. Actualizaciones y sincronización

Conserva y valida:

- versión instalada;
- última versión publicada;
- fecha de comprobación;
- resultado explícito: actualizado, actualización disponible o error;
- descarga del instalador;
- verificación SHA-256;
- cierre del proceso de Dimitry Hub antes de instalar;
- reapertura posterior;
- conservación de los datos del usuario.

Para la sincronización de datos:

- evita ejecuciones duplicadas;
- usa timeout;
- identifica la fuente que falla;
- no llena la pantalla con avisos repetidos;
- permite reintentar una sola fuente;
- conserva el último conjunto válido de datos cuando una fuente externa falla.

## 7. Manejo correcto del proceso

Cerrar la pestaña del navegador no equivale a cerrar Dimitry Hub.

Asegura que exista una acción visible “Cerrar Dimitry Hub” que:

- cierre el servidor local;
- libere el puerto;
- termine procesos secundarios;
- permita instalar una actualización sin abrir el Administrador de tareas.

El instalador también debe detectar y cerrar de forma controlada una instancia antigua.

## 8. Estados de interfaz y errores

Todas las páginas deben implementar:

- skeleton o mensaje de carga;
- contenido normal;
- estado vacío;
- error recuperable;
- botón de reintento cuando corresponda.

Nunca dejes el área principal en blanco por una excepción.

Añade un manejador global de errores JavaScript que registre el error y muestre una tarjeta recuperable en la interfaz. Guarda logs técnicos en:

`%LOCALAPPDATA%\Dimitry Hub\Logs`

No expongas claves ni datos sensibles en esos logs.

---

# Pruebas obligatorias

No declares terminado el trabajo solo porque compila.

## Pruebas unitarias y de API

Añade o corrige pruebas para:

- arranque de la aplicación;
- rutas principales;
- estado de actualizaciones;
- sincronización concurrente;
- creación de una sesión de save;
- validación de `Level.sav`;
- validación de `GlobalPalStorage.sav`;
- validación de jugador `.sav`;
- importación ZIP segura;
- rechazo de ZIP Slip y rutas peligrosas;
- creación de respaldo;
- restauración de respaldo;
- descarga de archivos editados;
- eliminación de sesión;
- comportamiento sin proveedor de IA.

## Pruebas de navegador

Añade Playwright o una alternativa equivalente y ejecuta, como mínimo:

1. La página inicial carga contenido no vacío.
2. Se hace clic en cada elemento del menú y cambia el encabezado y el contenido.
3. No aparecen errores de consola.
4. Palworld Wiki puede realizar una búsqueda local.
5. Editor Palworld muestra los cuatro tipos de carga.
6. Se crea una sesión usando fixtures de prueba no sensibles.
7. Se muestra respaldo y descarga.
8. Actualizaciones muestra versión instalada y última versión.
9. Ajustes cambia tema y tipografía una sola vez.
10. La acción Cerrar Dimitry Hub llama al endpoint correcto.

## Prueba del instalador de Windows

En GitHub Actions:

- compila ambos ejecutables;
- genera el instalador;
- instala de forma silenciosa en el runner cuando sea posible;
- inicia la aplicación instalada;
- espera a que responda el endpoint de salud;
- abre la interfaz con Playwright;
- ejecuta el smoke test completo;
- cierra el proceso;
- desinstala o limpia el entorno.

No publiques una release si falla cualquiera de estas pruebas.

---

# Seguridad y datos

- Conserva los datos del usuario durante actualizaciones.
- Nunca sobrescribas el save original sin una copia confirmada.
- Limita tamaños de subida.
- Sanitiza nombres de archivo.
- Evita traversal y ZIP Slip.
- No ejecutes contenido arbitrario incluido dentro de un ZIP.
- No almacenes claves de API en texto plano dentro del repositorio o logs.
- No desactives SmartScreen ni Defender.

---

# Criterios de aceptación finales

La tarea solo está terminada cuando:

1. La aplicación abre con contenido visible.
2. Todos los botones laterales funcionan.
3. Palworld Wiki y Editor Palworld están claramente separados.
4. La Wiki responde localmente sin IA.
5. El Editor acepta los cuatro tipos de entrada requeridos.
6. Cada sesión crea respaldo y copia de trabajo.
7. Las funciones visibles corresponden a capacidades reales.
8. No hay errores de consola durante el recorrido completo.
9. Las pruebas unitarias, API, navegador e instalador pasan.
10. Existe un artefacto de prueba de Windows.
11. Se adjuntan capturas o video corto de cada sección funcionando.
12. Se abre un pull request; no se publica automáticamente una release.

## Versión propuesta

Usa una versión de corrección como `1.1.1` si solo estabilizas lo ya anunciado. Usa `1.2.0` únicamente si la separación Wiki/Editor y el flujo completo del editor constituyen una ampliación funcional real.

## Entrega del pull request

Incluye:

- causa raíz de la pantalla vacía;
- resumen de arquitectura anterior y nueva;
- lista de archivos principales modificados;
- pruebas ejecutadas con resultado;
- capturas de Inicio, Palworld Wiki, Editor Palworld y Actualizaciones;
- instrucciones para construir e instalar;
- limitaciones reales pendientes;
- confirmación de que no se publicó una release automáticamente.
