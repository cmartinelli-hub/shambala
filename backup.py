import os
import gzip
import shutil
import glob
import tarfile
import tempfile
from datetime import date
import subprocess

CAMINHO_DB_BACKUP = os.path.join(os.path.dirname(__file__), "shamballa.db.backup")
PASTA_PROJETO   = os.path.dirname(__file__)
PASTA_LOCAL     = os.path.expanduser("~/Documentos/backup-shamballa")
MANTER_DIAS     = 30

# Padrões a excluir do backup completo
_EXCLUIR = {".venv", "__pycache__", ".git", "*.db", "*.db.backup", "*.pyc", "*.tar.gz"}


def _deve_excluir(caminho: str) -> bool:
    nome = os.path.basename(caminho)
    for padrao in _EXCLUIR:
        if padrao.startswith("*"):
            if nome.endswith(padrao[1:]):
                return True
        elif nome == padrao:
            return True
    return False


def _pg_dump(hoje: str) -> str:
    """Exporta o Postgres para arquivo SQL gzip temporário."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sql.gz", delete=False)
    try:
        env = os.environ.copy()
        env["PGPASSWORD"] = os.environ.get("SHAMBALA_DB_PASS", "")
        dbname = os.environ.get("SHAMBALA_DB_NAME", "shambala")
        dbuser = os.environ.get("SHAMBALA_DB_USER", "shambala")
        dbhost = os.environ.get("SHAMBALA_DB_HOST", "localhost")
        dbport = os.environ.get("SHAMBALA_DB_PORT", "5432")

        subprocess.run(
            [
                "pg_dump",
                "-h", dbhost,
                "-p", dbport,
                "-U", dbuser,
                "-F", "c",       # formato customizado
                "-f", tmp.name,
                dbname,
            ],
            env=env,
            check=True,
        )
        return tmp.name
    except Exception:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise


def _criar_pacote(pasta_destino: str, hoje: str, sql_backup_path: str = None) -> str:
    """Cria shamballa-YYYY-MM-DD.tar.gz com backup SQL + código."""
    nome_tar = f"shamballa-{hoje}.tar.gz"
    caminho_tar = os.path.join(pasta_destino, nome_tar)
    with tarfile.open(caminho_tar, "w:gz") as tar:
        # Dump SQL
        if sql_backup_path and os.path.exists(sql_backup_path):
            tar.add(sql_backup_path, arcname="shamballa.dump")
        # Arquivos do projeto
        for raiz, dirs, arquivos in os.walk(PASTA_PROJETO):
            dirs[:] = [d for d in dirs if not _deve_excluir(os.path.join(raiz, d))]
            for arq in arquivos:
                caminho_arq = os.path.join(raiz, arq)
                if not _deve_excluir(caminho_arq):
                    arcname = os.path.relpath(caminho_arq, PASTA_PROJETO)
                    tar.add(caminho_arq, arcname=arcname)
    return caminho_tar


def fazer_backup() -> list[str]:
    """
    Executa pg_dump, copia para pasta local e cria pacote completo
    no pendrive (se conectado).
    """
    hoje    = date.today().isoformat()
    msgs    = []
    sql_path = None

    # ── Pg_dump local ─────────────────────────────────────────────────────────
    try:
        sql_path = _pg_dump(hoje)
        os.makedirs(PASTA_LOCAL, exist_ok=True)
        destino_sql = os.path.join(PASTA_LOCAL, f"shamballa-{hoje}.dump")
        shutil.copy2(sql_path, destino_sql)
        msgs.append(f"Local (dump): {destino_sql}")

        # Apagar backups antigos
        todos = sorted(glob.glob(os.path.join(PASTA_LOCAL, "shamballa-*.dump")))
        for antigo in todos[:-MANTER_DIAS]:
            try:
                os.remove(antigo)
            except OSError:
                pass
    except Exception as e:
        msgs.append(f"pg_dump falhou ({e})")

    # ── Backup completo no pendrive ───────────────────────────────────────────
    pendrives = _detectar_pendrives()
    if pendrives:
        for ponto in pendrives:
            pasta_pen = os.path.join(ponto, "backup-shamballa")
            try:
                os.makedirs(pasta_pen, exist_ok=True)
                caminho_tar = _criar_pacote(pasta_pen, hoje, sql_path)
                msgs.append(f"Pendrive (completo): {caminho_tar}")
                # Manter só os últimos MANTER_DIAS pacotes
                tars = sorted(glob.glob(os.path.join(pasta_pen, "shamballa-*.tar.gz")))
                for antigo in tars[:-MANTER_DIAS]:
                    try:
                        os.remove(antigo)
                    except OSError:
                        pass
            except Exception as e:
                msgs.append(f"Pendrive {ponto}: falhou ({e})")
    else:
        # Sem pendrive: cria pacote completo local também
        try:
            caminho_tar = _criar_pacote(PASTA_LOCAL, hoje, sql_path)
            msgs.append(f"Local (completo): {caminho_tar}")
        except Exception as e:
            msgs.append(f"Pacote completo local: falhou ({e})")
        msgs.append("Pendrive: nenhum conectado")

    # Limpando temp
    if sql_path and os.path.exists(sql_path):
        try:
            os.unlink(sql_path)
        except OSError:
            pass

    return msgs


def _detectar_pendrives() -> list[str]:
    """Retorna pontos de montagem de pendrives em /media/."""
    pontos = []
    raiz_media = "/media"
    if not os.path.isdir(raiz_media):
        return pontos
    for entrada in glob.glob(os.path.join(raiz_media, "*", "*")) + \
                   glob.glob(os.path.join(raiz_media, "*")):
        if os.path.ismount(entrada):
            pontos.append(entrada)
    return pontos
