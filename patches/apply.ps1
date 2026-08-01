param(
  [Parameter(Mandatory = $true)]
  [string]$Root
)

$ErrorActionPreference = 'Stop'
$appJs = Join-Path $Root 'app/static/app.js'
if (-not (Test-Path $appJs)) {
  throw "No se encontró $appJs"
}

$text = Get-Content $appJs -Raw

# El cuerpo de la página también tiene data-theme y data-font. Los selectores
# genéricos le asignaban los manejadores de los botones al <body>, por lo que
# cualquier clic en cualquier pestaña volvía a aplicar tema y tipografía.
$text = $text.Replace(
  "document.querySelectorAll('[data-theme]')",
  "document.querySelectorAll('.theme-card[data-theme]')"
)
$text = $text.Replace(
  "document.querySelectorAll('[data-font]')",
  "document.querySelectorAll('.font-card[data-font]')"
)

# Evita guardar otra vez la misma selección y bloquea clics repetidos mientras
# se está aplicando un cambio visual.
$text = $text.Replace(
  "async function applyTheme(theme){document.body.dataset.theme=theme;state.settings.theme=theme;document.querySelectorAll('.theme-card[data-theme]').forEach(b=>b.classList.toggle('active',b.dataset.theme===theme));await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme})});toast('Tema aplicado')}",
  "let appearanceBusy=false;async function applyTheme(theme){if(appearanceBusy||!theme||state.settings.theme===theme)return;appearanceBusy=true;try{document.body.dataset.theme=theme;state.settings.theme=theme;document.querySelectorAll('.theme-card[data-theme]').forEach(b=>b.classList.toggle('active',b.dataset.theme===theme));await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme})});toast('Tema aplicado')}catch(error){toast(error.message,'error')}finally{appearanceBusy=false}}"
)
$text = $text.Replace(
  "async function applyFont(font){document.body.dataset.font=font;state.settings.font_style=font;document.querySelectorAll('.font-card[data-font]').forEach(b=>b.classList.toggle('active',b.dataset.font===font));await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({font_style:font})});toast('Tipografía aplicada')}",
  "async function applyFont(font){if(appearanceBusy||!font||state.settings.font_style===font)return;appearanceBusy=true;try{document.body.dataset.font=font;state.settings.font_style=font;document.querySelectorAll('.font-card[data-font]').forEach(b=>b.classList.toggle('active',b.dataset.font===font));await api('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({font_style:font})});toast('Tipografía aplicada')}catch(error){toast(error.message,'error')}finally{appearanceBusy=false}}"
)

Set-Content $appJs -Value $text -Encoding utf8
Write-Host 'Corrección global de eventos de apariencia aplicada.'
