import subprocess
from config import REPO_DIR, CSV_DIR, QUARANTINE_DIR, GITHUB_REPO, GITHUB_BRANCH, GITHUB_TOKEN
from utils import log

def setup_git():
    if not GITHUB_TOKEN or GITHUB_REPO == "tu_usuario/pso2clasic":
        log("ERROR CRÍTICO: Configura GITHUB_TOKEN o GITHUB_REPO.")
        return False
        
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)

    remote_url = f"https://oauth2:{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"

    if not (REPO_DIR / ".git").exists():
        subprocess.run(["git", "init"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "branch", "-m", GITHUB_BRANCH], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)
    else:
        remotes = subprocess.run(["git", "remote"], cwd=REPO_DIR, capture_output=True, text=True)
        if "origin" in remotes.stdout.split():
            subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR, check=False)
        else:
            subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)

    fetch = subprocess.run(
        ["git", "fetch", "origin", GITHUB_BRANCH],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        log(f"Error al conectar con GitHub: {fetch.stderr.strip() or fetch.stdout.strip()}")
        return False

    subprocess.run(["git", "branch", "-u", f"origin/{GITHUB_BRANCH}", GITHUB_BRANCH], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "reset", "--mixed", f"origin/{GITHUB_BRANCH}"], cwd=REPO_DIR, check=False)
    return True

def pull_from_github():
    res = subprocess.run(["git", "pull", "origin", GITHUB_BRANCH], cwd=REPO_DIR, capture_output=True, text=True)
    out = res.stdout.strip()
    if "Already up to date" not in out and res.returncode == 0:
        log("Nuevos archivos descargados correctamente.")

def push_to_github():
    if not CSV_DIR.exists(): return
    for path in ("archivos a traducir", "listo", "Cuarentena"):
        target = REPO_DIR / path
        if target.exists():
            subprocess.run(["git", "add", f"{path}/"], cwd=REPO_DIR, check=False)
        
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip(): return
        
    log("Guardando cambios en GitHub (Push)...")
    subprocess.run(["git", "commit", "-m", "Bot: Traducciones, Raw Healing y Cuarentena"], cwd=REPO_DIR, check=False)
    subprocess.run(["git", "push", "origin", f"HEAD:{GITHUB_BRANCH}"], cwd=REPO_DIR, check=False)
