from __future__ import annotations

import sys
from pathlib import Path


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"No se encontró el bloque requerido: {label}")
    return text.replace(old, new, 1)


def patch_app_js(root: Path) -> None:
    path = root / "app/static/app.js"
    text = path.read_text(encoding="utf-8")
    start = text.index("async function renderUpdates(){")
    end = text.index("\nfunction sourceCard", start)
    replacement = r'''function appUpdateStatusText(update){if(update.checking)return 'Comprobando el canal oficial…';if(update.error)return 'No se pudo comprobar la versión';if(update.available)return `Nueva versión ${update.latest_version||''} disponible`;if(update.last_checked)return `Sin actualizaciones · comprobado ${fmtDate(update.last_checked)}`;return 'Pulsa Comprobar la aplicación para revisar el canal oficial'}
function appVersionCards(update){return `<div class="stats-grid grid" style="grid-template-columns:1fr 1fr"><div class="stat-card"><span class="stat-icon">V</span><div><b>${esc(update.current_version||'—')}</b><small>VERSIÓN INSTALADA</small></div></div><div class="stat-card"><span class="stat-icon">↻</span><div><b>${esc(update.latest_version||'—')}</b><small>ÚLTIMA VERSIÓN PUBLICADA</small></div></div></div>`}
async function installAppUpdate(button){if(button)button.disabled=true;toast('Descargando y verificando la actualización…');try{await api('/api/app-update/install',{method:'POST'});toast('La aplicación se cerrará, instalará la actualización y volverá a abrirse.')}catch(error){toast(error.message,'error');if(button)button.disabled=false}}
function showAppUpdateResult(update){const current=update.current_version||'—';const latest=update.latest_version||current;if(update.error){openModal('No se pudo comprobar la aplicación',`${appVersionCards(update)}<div class="notice bad" style="margin-top:14px">${esc(update.error)}</div>`);return}if(update.available){openModal('Actualización disponible',`${appVersionCards(update)}${update.notes?`<div class="notice info" style="margin-top:14px">${esc(update.notes)}</div>`:''}<div class="form-actions"><button class="btn primary" id="installUpdateFromResult">Instalar versión ${esc(latest)}</button></div>`);const install=document.querySelector('#installUpdateFromResult');if(install)install.onclick=()=>installAppUpdate(install);return}openModal('Dimitry Hub está actualizado',`${appVersionCards(update)}<div class="notice info" style="margin-top:14px"><b>No se encontraron actualizaciones.</b><br>Tu versión ${esc(current)} coincide con la última versión publicada.</div><p style="margin-top:12px;color:var(--muted)">Última comprobación: ${esc(fmtDate(update.last_checked))}</p>`)}
async function checkApplicationUpdate(button){if(button){button.disabled=true;button.textContent='Comprobando…'}try{const result=await api('/api/app-update/check',{method:'POST'});if(result.error)toast('No se pudo comprobar la actualización','error');else if(result.available)toast(`Nueva versión ${result.latest_version} disponible`,'ok');else toast(`Sin actualizaciones · versión ${result.current_version}`,'ok');await renderUpdates();showAppUpdateResult(result)}catch(error){toast(error.message,'error')}finally{if(button){button.disabled=false;button.textContent='Comprobar la aplicación'}}}
async function renderUpdates(){
  setHead('Actualizaciones','APP, FUENTES Y SINCRONIZACIÓN');loading('Revisando el estado');
  try{const [data,appUpdate]=await Promise.all([api('/api/sync/status'),api('/api/app-update/status')]);state.sync=data;const mode=data.settings.auto_sync_mode||'safe';const appState=appUpdateStatusText(appUpdate);view.innerHTML=`<section class="hero blue"><div class="hero-content"><span class="eyebrow">CENTRO DE ACTUALIZACIONES</span><h2>La aplicación y sus bibliotecas se mantienen al día.</h2><p>Dimitry Hub comprueba automáticamente el canal oficial. Las actualizaciones verifican su SHA-256 y conservan tus datos fuera de la carpeta del programa.</p><div class="hero-actions"><button class="btn white" id="checkAppUpdate">Comprobar la aplicación</button>${appUpdate.available?'<button class="btn ghost" id="installAppUpdate">Instalar ahora</button>':''}<button class="btn ghost" id="syncAll">Sincronizar datos</button></div></div></section><div class="section-head"><div><h2>Aplicación</h2><p>${esc(appState)}</p></div></div><section class="grid two-col"><article class="panel"><div class="panel-head"><div><h3>Dimitry Hub</h3><p>Canal oficial: GitHub Releases</p></div><span class="power-badge ${appUpdate.available?'warn':''}"><i></i>${appUpdate.available?'Actualización disponible':appUpdate.error?'Comprobación fallida':'Actualizado'}</span></div>${appVersionCards(appUpdate)}${appUpdate.last_checked?`<p style="margin-top:12px;color:var(--muted)">Última comprobación: ${esc(fmtDate(appUpdate.last_checked))}</p>`:''}${appUpdate.notes?`<div class="notice info" style="margin-top:14px">${esc(appUpdate.notes)}</div>`:''}${appUpdate.error?notice(appUpdate.error,'bad'):''}</article><aside class="panel"><h3>Actualización segura</h3><p>La nueva versión se descarga, verifica, instala en silencio y vuelve a abrir la aplicación. Proyectos, documentos, saves importados y ajustes permanecen intactos.</p><div class="notice info">Los datos se almacenan en AppData\\Local\\Dimitry Hub\\Data.</div></aside></section><div class="section-head"><div><h2>Fuentes conectadas</h2><p>Modo actual: ${esc(mode==='manual'?'Manual':mode==='safe'?'Automático seguro':'Automático')}</p></div></div><section class="source-grid">${data.sources.map(sourceCard).join('')}</section><div class="section-head"><div><h2>Actividad reciente</h2></div></div><section class="grid two-col"><article class="panel"><div class="list">${data.events.map(eventRow).join('')||'<div class="empty"><b>Sin actividad</b>Ejecuta la primera sincronización.</div>'}</div></article><aside class="panel"><h3>Últimas ejecuciones</h3><div class="list">${data.runs.map(r=>`<div class="list-row"><div><h4>${esc(r.status)}</h4><small>${fmtDate(r.finished_at||r.started_at)} · ${r.changed_count} fuente(s) con cambios</small></div></div>`).join('')||'<div class="empty">Sin ejecuciones.</div>'}</div></aside></section>`;bindLaunch();document.querySelector('#syncAll').onclick=runSync;document.querySelector('#checkAppUpdate').onclick=e=>checkApplicationUpdate(e.currentTarget);const install=document.querySelector('#installAppUpdate');if(install)install.onclick=()=>installAppUpdate(install)}catch(error){view.innerHTML=notice(error.message,'bad')}
}'''
    text = text[:start] + replacement + text[end:]
    path.write_text(text, encoding="utf-8")


def patch_index(root: Path) -> None:
    path = root / "app/static/index.html"
    text = path.read_text(encoding="utf-8")
    text = replace_required(
        text,
        '<button class="nav-item advanced-nav" data-route="updates"><span>↻</span>Actualizaciones</button>',
        '<button class="nav-item" data-route="updates"><span>↻</span>Actualizaciones</button>',
        "acceso lateral de Actualizaciones",
    )
    path.write_text(text, encoding="utf-8")


def patch_sync(root: Path) -> None:
    path = root / "app/sync_engine.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('from urllib.parse import urlparse', 'from urllib.parse import quote, urlparse')
    text = text.replace('GITHUB_API_VERSION = "2026-03-10"', 'GITHUB_API_VERSION = "2022-11-28"')

    start = text.index('def _resolve_github_version(')
    end = text.index('\ndef sync_palworld_editor', start)
    resolver = '''def _resolve_github_version(owner: str, repo: str, settings: dict[str, str]) -> dict:
    headers = _github_headers(settings)
    safe_owner = quote(owner, safe="")
    safe_repo = quote(repo, safe="")
    release_url = f"https://api.github.com/repos/{safe_owner}/{safe_repo}/releases/latest"
    try:
        release = http_get(release_url, timeout=45, max_bytes=3 * 1024 * 1024, headers=headers).json()
        if isinstance(release, dict) and release.get("tag_name"):
            tag = str(release["tag_name"])
            safe_tag = quote(tag, safe="")
            return {
                "version": tag,
                "download_url": f"https://codeload.github.com/{safe_owner}/{safe_repo}/zip/refs/tags/{safe_tag}",
                "fallback_url": f"https://github.com/{safe_owner}/{safe_repo}/archive/refs/tags/{safe_tag}.zip",
                "html_url": release.get("html_url", ""),
                "published_at": release.get("published_at", ""),
                "mode": "release",
                "notes": release.get("body", "") or "",
            }
    except Exception:
        pass
    repo_info = http_get(
        f"https://api.github.com/repos/{safe_owner}/{safe_repo}", timeout=45, max_bytes=2 * 1024 * 1024, headers=headers,
    ).json()
    if not isinstance(repo_info, dict):
        raise RuntimeError("GitHub no devolvió los datos esperados del repositorio")
    branch = str(repo_info.get("default_branch") or "main")
    commit = http_get(
        f"https://api.github.com/repos/{safe_owner}/{safe_repo}/commits/{quote(branch, safe='')}", timeout=45, max_bytes=3 * 1024 * 1024, headers=headers,
    ).json()
    sha = str(commit.get("sha") or "") if isinstance(commit, dict) else ""
    if not sha:
        raise RuntimeError("No se pudo determinar la versión actual del editor")
    return {
        "version": sha,
        "download_url": f"https://codeload.github.com/{safe_owner}/{safe_repo}/zip/{sha}",
        "fallback_url": f"https://github.com/{safe_owner}/{safe_repo}/archive/{sha}.zip",
        "html_url": repo_info.get("html_url", ""),
        "published_at": commit.get("commit", {}).get("committer", {}).get("date", "") if isinstance(commit, dict) else "",
        "mode": "commit",
        "notes": commit.get("commit", {}).get("message", "") if isinstance(commit, dict) else "",
        "branch": branch,
    }

'''
    text = text[:start] + resolver + text[end + 1:]

    old_download = '''        size, digest, response_headers = download_to_file(
            resolved["download_url"], target, timeout=150, max_bytes=350 * 1024 * 1024,
            headers=_github_headers(settings),
        )'''
    new_download = '''        download_error: Exception | None = None
        for download_url in [resolved.get("download_url"), resolved.get("fallback_url")]:
            if not download_url:
                continue
            try:
                size, digest, response_headers = download_to_file(
                    str(download_url), target, timeout=150, max_bytes=350 * 1024 * 1024,
                    headers=None,
                )
                download_error = None
                break
            except Exception as exc:
                download_error = exc
        if download_error is not None:
            raise RuntimeError(f"No se pudo descargar el archivo técnico desde GitHub: {download_error}")'''
    text = replace_required(text, old_download, new_download, "descarga del editor de Palworld")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python patches/apply_v104.py <directorio-fuente>")
    root = Path(sys.argv[1]).resolve()
    patch_app_js(root)
    patch_index(root)
    patch_sync(root)
    print("Correcciones 1.0.4 aplicadas correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
