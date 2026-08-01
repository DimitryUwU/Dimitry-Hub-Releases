from __future__ import annotations

import sys
from pathlib import Path


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"No se encontró el bloque requerido: {label}")
    return text.replace(old, new)


def patch_app_js(root: Path) -> None:
    path = root / "app/static/app.js"
    text = path.read_text(encoding="utf-8")

    # El body también posee data-theme y data-font. Los selectores originales
    # terminaban asignando los eventos al cuerpo entero de la aplicación.
    text = text.replace(
        "document.querySelectorAll('[data-theme]')",
        "document.querySelectorAll('.theme-card[data-theme]')",
    )
    text = text.replace(
        "document.querySelectorAll('[data-font]')",
        "document.querySelectorAll('.font-card[data-font]')",
    )

    old_sync = "async function runSync(){const buttons=[...document.querySelectorAll('#syncAll,#palSync,#quickSync')];buttons.forEach(b=>{b.disabled=true});document.querySelector('#connectionText').textContent='Actualizando…';try{const data=await api('/api/sync/all',{method:'POST'});const errors=data.results?.filter(x=>x.error)||[];toast(errors.length?`Sincronización parcial: ${errors.length} fuente(s) con error`:`Actualización completada: ${data.changed_count} fuente(s) cambiaron`,errors.length?'error':'ok');if(state.route==='updates')renderUpdates();else if(state.route==='palworld')renderPalworld()}catch(error){toast(error.message,'error')}finally{buttons.forEach(b=>{b.disabled=false});document.querySelector('#connectionText').textContent='Preparado'}}"
    new_sync = """function syncSourceName(key){return ({'palworld-steam-news':'Noticias oficiales de Palworld','palworld-editor-github':'Editor técnico de Palworld'})[key]||key||'Fuente externa'}
async function runSync(){const buttons=[...document.querySelectorAll('#syncAll,#palSync,#quickSync')];buttons.forEach(b=>{b.disabled=true});const statusText=document.querySelector('#connectionText');if(statusText)statusText.textContent='Sincronizando…';try{const data=await api('/api/sync/all',{method:'POST'});const errors=data.results?.filter(x=>x.error)||[];if(errors.length){const details=errors.map(item=>`<div class=\"notice bad\"><b>${esc(syncSourceName(item.source))}</b><br>${esc(item.error)}</div>`).join('');toast(`Sincronización parcial: ${errors.length} fuente(s) con error`,'error');openModal('Sincronización parcial',`${details}<div class=\"form-actions\"><button class=\"btn\" id=\"openUpdatesFromSync\">Ver centro de actualizaciones</button></div>`);const open=document.querySelector('#openUpdatesFromSync');if(open)open.onclick=()=>{closeModal();navigate('updates')}}else{toast(`Datos actualizados: ${data.changed_count} fuente(s) cambiaron`,'ok')}if(state.route==='updates')renderUpdates();else if(state.route==='palworld')renderPalworld()}catch(error){toast(error.message,'error')}finally{buttons.forEach(b=>{b.disabled=false});if(statusText)statusText.textContent='Preparado'}}"""
    text = replace_required(text, old_sync, new_sync, "runSync")

    old_bind = "function bindGlobal(){document.querySelectorAll('[data-route]').forEach(b=>b.onclick=()=>navigate(b.dataset.route));document.querySelector('#menuBtn').onclick=()=>document.querySelector('#sidebar').classList.toggle('open');document.querySelector('#modalClose').onclick=closeModal;modal.onclick=e=>{if(e.target===modal)closeModal()};document.querySelector('#quickSync').onclick=runSync;document.querySelector('#profileChip').onclick=()=>navigate('settings');}"
    new_bind = """async function openProfilePanel(){try{const [ai,update]=await Promise.all([api('/api/ai/status'),api('/api/app-update/status')]);const ready=ai.providers?.openai?.configured||ai.providers?.ollama?.online||ai.providers?.compatible?.configured;const name=state.settings.display_name||'Dimitry';openModal('Perfil y control rápido',`<div class=\"profile-summary\"><div class=\"avatar\" style=\"display:grid;place-items:center;margin-bottom:12px\">${esc(name.split(/\\s+/).map(x=>x[0]).join('').slice(0,2).toUpperCase())}</div><h3>${esc(name)}</h3><p>Dimitry Hub ${esc(update.current_version||'')}</p></div><div class=\"notice ${ready?'info':'bad'}\">${ready?'Dimitry AI está preparada.':'Dimitry AI todavía no tiene ningún proveedor configurado.'}</div><div class=\"form-actions\"><button class=\"btn primary\" id=\"profileAI\">${ready?'Abrir Dimitry AI':'Configurar IA'}</button><button class=\"btn\" id=\"profileUpdates\">Actualizaciones</button><button class=\"btn danger\" id=\"profileCloseApp\">Cerrar Dimitry Hub</button></div>`);document.querySelector('#profileAI').onclick=()=>{closeModal();navigate(ready?'assistant':'settings')};document.querySelector('#profileUpdates').onclick=()=>{closeModal();navigate('updates')};document.querySelector('#profileCloseApp').onclick=shutdownApp}catch(error){toast(error.message,'error')}}
async function shutdownApp(){const button=document.querySelector('#profileCloseApp');if(button)button.disabled=true;try{await api('/api/system/shutdown',{method:'POST'});document.querySelector('#modalBody').innerHTML='<div class=\"notice info\"><b>Dimitry Hub se está cerrando.</b><br>Ya puedes cerrar esta pestaña o ejecutar el instalador.</div>'}catch(error){toast(error.message,'error');if(button)button.disabled=false}}
function bindGlobal(){document.querySelectorAll('[data-route]').forEach(b=>b.onclick=()=>navigate(b.dataset.route));document.querySelector('#menuBtn').onclick=()=>document.querySelector('#sidebar').classList.toggle('open');document.querySelector('#modalClose').onclick=closeModal;modal.onclick=e=>{if(e.target===modal)closeModal()};document.querySelector('#quickSync').onclick=runSync;document.querySelector('#profileChip').onclick=openProfilePanel;}"""
    text = replace_required(text, old_bind, new_bind, "bindGlobal")

    text = text.replace("GPT-5.6 · razonamiento alto", "Modelo configurado · razonamiento alto")
    text = text.replace("GPT-5.6 Terra · razonamiento medio", "Modelo configurado · razonamiento medio")
    text = text.replace("GPT-5.6 Luna · razonamiento bajo", "Modelo configurado · razonamiento bajo")

    old_preset = "function applyAIPreset(profile){const profiles={max:{model:'gpt-5.6',effort:'high',pro:false,label:'Máxima'},balanced:{model:'gpt-5.6-terra',effort:'medium',pro:false,label:'Equilibrada'},fast:{model:'gpt-5.6-luna',effort:'low',pro:false,label:'Rápida'}};const item=profiles[profile];if(!item)return;const model=document.querySelector('#openaiModel');if(model&&!Array.from(model.options).some(o=>o.value===item.model))model.add(new Option(item.model,item.model));if(model)model.value=item.model;document.querySelector('#reasoningEffort').value=item.effort;document.querySelector('#proMode').checked=item.pro;document.querySelectorAll('[data-ai-preset]').forEach(b=>b.classList.toggle('active',b.dataset.aiPreset===profile));toast(`Perfil ${item.label} seleccionado. Pulsa Guardar todos los ajustes.`)}"
    new_preset = "function applyAIPreset(profile){const ready=state.aiStatus?.providers?.openai?.configured||state.aiStatus?.providers?.ollama?.online||state.aiStatus?.providers?.compatible?.configured;if(!ready){toast('Configura primero OpenAI, Ollama o un proveedor compatible','error');return}const profiles={max:{effort:'high',pro:true,label:'Máxima'},balanced:{effort:'medium',pro:false,label:'Equilibrada'},fast:{effort:'low',pro:false,label:'Rápida'}};const item=profiles[profile];if(!item)return;document.querySelector('#reasoningEffort').value=item.effort;document.querySelector('#proMode').checked=item.pro;document.querySelectorAll('[data-ai-preset]').forEach(b=>b.classList.toggle('active',b.dataset.aiPreset===profile));toast(`Perfil ${item.label} seleccionado. Pulsa Guardar todos los ajustes.`)}"
    text = replace_required(text, old_preset, new_preset, "applyAIPreset")

    old_assistant_hook = "document.querySelector('#sendAI').onclick=sendAIMessage;document.querySelector('#aiMessage').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendAIMessage()}};if(state.aiThread)await openAIThread(state.aiThread)"
    new_assistant_hook = "document.querySelector('#sendAI').onclick=sendAIMessage;document.querySelector('#aiMessage').onkeydown=e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendAIMessage()}};if(!ready){document.querySelector('#sendAI').disabled=true;document.querySelector('#aiMessage').disabled=true;document.querySelector('#aiMessage').placeholder='Configura una IA en Ajustes para comenzar.'}if(state.aiThread)await openAIThread(state.aiThread)"
    text = replace_required(text, old_assistant_hook, new_assistant_hook, "estado de Dimitry AI")

    path.write_text(text, encoding="utf-8")


def patch_index(root: Path) -> None:
    path = root / "app/static/index.html"
    text = path.read_text(encoding="utf-8")
    text = replace_required(
        text,
        '<button class="sync-mini" id="quickSync"><span>↻</span><b>Actualizar</b></button>',
        '<button class="sync-mini" id="quickSync" title="Sincronizar las bibliotecas y datos conectados"><span>↻</span><b>Sincronizar datos</b></button>',
        "botón de sincronización",
    )
    text = text.replace(
        '<button class="avatar" id="profileChip" title="Perfil">DV</button>',
        '<button class="avatar" id="profileChip" title="Perfil y control rápido">DV</button>',
    )
    path.write_text(text, encoding="utf-8")


def patch_sync(root: Path) -> None:
    path = root / "app/sync_engine.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace('GITHUB_API_VERSION = "2026-03-10"', 'GITHUB_API_VERSION = "2022-11-28"')
    path.write_text(text, encoding="utf-8")


def patch_main(root: Path) -> None:
    path = root / "app/main.py"
    text = path.read_text(encoding="utf-8")
    if "import os\n" not in text:
        text = text.replace("import io\n", "import io\nimport os\n")
    old = '''@app.post("/api/app-update/install")
def install_app_update() -> dict:
    result = install_available_update()
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


@app.get("/api/dashboard")
'''
    new = '''@app.post("/api/app-update/install")
def install_app_update() -> dict:
    result = install_available_update()
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result


def _shutdown_after_response() -> None:
    time.sleep(0.8)
    os._exit(0)


@app.post("/api/system/shutdown")
def shutdown_system() -> dict:
    threading.Thread(target=_shutdown_after_response, name="dimitry-shutdown", daemon=True).start()
    return {"status": "closing"}


@app.get("/api/dashboard")
'''
    text = replace_required(text, old, new, "endpoint de apagado")
    path.write_text(text, encoding="utf-8")


def patch_updater(root: Path) -> None:
    path = root / "updater.py"
    path.write_text('''from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_process(pid: int) -> None:
    if os.name == "nt" and process_alive(pid):
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--restart", required=True)
    args = parser.parse_args()

    installer = Path(args.installer).resolve()
    restart = Path(args.restart).resolve()
    if not installer.exists():
        return 2

    deadline = time.time() + 20
    while process_alive(args.wait_pid) and time.time() < deadline:
        time.sleep(0.35)
    if process_alive(args.wait_pid):
        stop_process(args.wait_pid)
        time.sleep(1.0)

    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            str(installer),
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
        ],
        creationflags=flags,
    )
    if result.returncode != 0:
        return result.returncode

    if restart.exists():
        subprocess.Popen([str(restart)], creationflags=flags)

    if os.name == "nt":
        me = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
        command = (
            'timeout /t 3 /nobreak >nul & '
            f'del /f /q "{installer}" >nul 2>&1 & '
            f'del /f /q "{me}" >nul 2>&1'
        )
        subprocess.Popen(["cmd", "/d", "/c", command], creationflags=flags)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")


def patch_installer(root: Path) -> None:
    path = root / "installer.iss"
    text = path.read_text(encoding="utf-8")
    if "[Code]" not in text:
        text += '''
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/D /C taskkill /F /T /IM DimitryHub.exe >nul 2>&1', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(700);
  Result := '';
end;
'''
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python patches/apply.py <directorio-fuente>")
    root = Path(sys.argv[1]).resolve()
    patch_app_js(root)
    patch_index(root)
    patch_sync(root)
    patch_main(root)
    patch_updater(root)
    patch_installer(root)
    print("Correcciones de Dimitry Hub aplicadas correctamente.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
