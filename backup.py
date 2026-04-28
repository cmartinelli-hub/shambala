import os
import gzip
import shutil
import glob
import tarfile
import tempfile
from datetime import date
import subprocess

# ── Carregar .env se disponível ──────────────────────────────────────────────
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("'\"")
                os.environ.setdefault(key, val)

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
    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ.get("SHAMBALA_DB_PASS", "")
    dbname = os.environ.get("SHAMBALA_DB_NAME", "shambala")
    dbuser = os.environ.get("SHAMBALA_DB_USER", "shambala")
    dbhost = os.environ.get("SHAMBALA_DB_HOST", "localhost")
    dbport = os.environ.get("SHAMBALA_DB_PORT", "5432")

    tmp_sql = tempfile.NamedTemporaryFile(suffix=".sql", delete=False)
    tmp_gz = tmp_sql.name + ".gz"
    tmp_sql.close()

    try:
        # Executa pg_dump → arquivo SQL
        result = subprocess.run(
            [
                "pg_dump",
                "-h", dbhost,
                "-p", dbport,
                "-U", dbuser,
                "--no-owner",
                "--no-privileges",
                dbname,
            ],
            env=env,
            check=True,
            stdout=open(tmp_sql.name, "w"),
            stderr=subprocess.PIPE,
        )

        # Comprime com gzip
        with open(tmp_sql.name, "rb") as f_in:
            with gzip.open(tmp_gz, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.unlink(tmp_sql.name)

        return tmp_gz
    except Exception as e:
        if os.path.exists(tmp_sql.name):
            os.unlink(tmp_sql.name)
        if os.path.exists(tmp_gz):
            os.unlink(tmp_gz)
        raise


def _criar_pacote(pasta_destino: str, hoje: str, sql_backup_path: str = None) -> str:
    """Cria shamballa-YYYY-MM-DD.tar.gz com backup SQL + código."""
    nome_tar = f"shamballa-{hoje}.tar.gz"
    caminho_tar = os.path.join(pasta_destino, nome_tar)
    with tarfile.open(caminho_tar, "w:gz") as tar:
        # Dump SQL
        if sql_backup_path and os.path.exists(sql_backup_path):
            tar.add(sql_backup_path, arcname="shamballa.sql.gz")
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
        destino_sql = os.path.join(PASTA_LOCAL, f"shamballa-{hoje}.sql.gz")
        shutil.copy2(sql_path, destino_sql)
        msgs.append(f"PostgreSQL (dump): {destino_sql}")

        # Apagar backups antigos
        todos = sorted(glob.glob(os.path.join(PASTA_LOCAL, "shamballa-*.sql.gz")))
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

    # ── Enviar para servidor remoto ───────────────────────────────────
    arquivos_enviar = []
    destino_sql = os.path.join(PASTA_LOCAL, f"shamballa-{hoje}.sql.gz")
    if os.path.exists(destino_sql):
        arquivos_enviar.append(destino_sql)
    destino_tar = os.path.join(PASTA_LOCAL, f"shamballa-{hoje}.tar.gz")
    if os.path.exists(destino_tar):
        arquivos_enviar.append(destino_tar)

    if arquivos_enviar:
        msgs.extend(_enviar_remoto(arquivos_enviar, hoje))

    return msgs


def _ler_config_backup_bd() -> dict:
    """Lê configurações de backup do banco de dados."""
    try:
        from banco import conectar
        with conectar() as conn:
            rows = conn.execute(
                "SELECT chave, valor FROM configuracoes_backup"
            ).fetchall()
            config = {}
            for r in rows:
                config[r["chave"]] = r["valor"]
            return config
    except Exception:
        return {}


def _enviar_remoto(caminhos: list[str], hoje: str) -> list[str]:
    """Envia backups para um servidor remoto via SCP."""
    msgs = []
    config = _ler_config_backup_bd()
    host = config.get("backup_host", "").strip()
    if not host:
        return msgs  # Sem host configurado, silencia

    user = config.get("backup_user", "").strip()
    path = config.get("backup_path", "").strip()
    ssh_key = config.get("backup_ssh_key_path", "").strip()

    remoto = f"{user}@{host}" if user else host
    ssh_opts = ["-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]
    if ssh_key and os.path.exists(ssh_key):
        ssh_opts.extend(["-i", ssh_key])

    # Criar pasta remota via SSH
    try:
        subprocess.run(
            ["ssh"] + ssh_opts + [remoto, f"mkdir -p {path}"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception as e:
        msgs.append(f"Remoto ({host}): falha ao criar pasta ({e})")
        return msgs

    # Enviar cada arquivo via SCP
    for caminho in caminhos:
        if not os.path.exists(caminho):
            continue
        nome_arq = os.path.basename(caminho)
        try:
            subprocess.run(
                ["scp"] + ssh_opts + [caminho, f"{remoto}:{path}/"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
            msgs.append(f"Remoto ({host}): {nome_arq} enviado")
        except Exception as e:
            msgs.append(f"Remoto ({host}): falha ao enviar {nome_arq} ({e})")

    # Manter só os últimos MANTER_DIAS no remoto
    try:
        result = subprocess.run(
            ["ssh"] + ssh_opts + [remoto, f"ls -1t {path}/shamballa-*.sql.gz 2>/dev/null"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            text=True,
        )
        if result.stdout.strip():
            arquivos_remotos = result.stdout.strip().split("\n")
            for antigo in arquivos_remotos[MANTER_DIAS:]:
                subprocess.run(
                    ["ssh"] + ssh_opts + [remoto, f"rm -f {antigo.strip()}"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
    except Exception:
        pass  # Falha na limpeza remota não é crítica

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


def restaurar_backup(caminho_sql_gz: str) -> list[str]:
    """
    Restaura um backup .sql.gz para o banco PostgreSQL.
    Retorna lista de mensagens (sucesso/erro).
    """
    msgs = []

    # Validar extensão
    if not caminho_sql_gz.endswith(".sql.gz"):
        msgs.append("Formato inválido: esperado arquivo .sql.gz")
        return msgs

    # Validar que o caminho está dentro dos diretórios permitidos
    _DIRS_PERMITIDOS = [PASTA_LOCAL, "/media", PASTA_PROJETO]
    caminho_abs = os.path.realpath(caminho_sql_gz)
    if not any(caminho_abs.startswith(os.path.realpath(d)) for d in _DIRS_PERMITIDOS):
        msgs.append("Caminho não permitido para restauração.")
        return msgs

    if not os.path.exists(caminho_abs):
        msgs.append(f"Arquivo não encontrado: {caminho_sql_gz}")
        return msgs

    env = os.environ.copy()
    env["PGPASSWORD"] = os.environ.get("SHAMBALA_DB_PASS", "")
    dbname = os.environ.get("SHAMBALA_DB_NAME", "shambala")
    dbuser = os.environ.get("SHAMBALA_DB_USER", "shambala")
    dbhost = os.environ.get("SHAMBALA_DB_HOST", "localhost")
    dbport = os.environ.get("SHAMBALA_DB_PORT", "5432")

    tmp_sql = None
    try:
        # Descomprimir para arquivo temporário seguro
        with tempfile.NamedTemporaryFile(suffix=".sql", delete=False) as tmp:
            tmp_sql = tmp.name

        with gzip.open(caminho_abs, "rb") as f_in:
            with open(tmp_sql, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)

        # Restaurar
        subprocess.run(
            [
                "psql",
                "-h", dbhost,
                "-p", dbport,
                "-U", dbuser,
                "-d", dbname,
                "-f", tmp_sql,
                "--quiet",
            ],
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        msgs.append(f"Restauração concluída com sucesso em {dbname}@{dbhost}")
    except subprocess.CalledProcessError as e:
        msgs.append(f"Erro ao restaurar: {e.stderr.decode('utf-8', errors='replace')}")
    except Exception as e:
        msgs.append(f"Erro: {e}")
    finally:
        if tmp_sql and os.path.exists(tmp_sql):
            os.unlink(tmp_sql)

    return msgs


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "restaurar":
        if len(sys.argv) < 3:
            print("Uso: python backup.py restaurar <caminho_do_backup.sql.gz>")
            sys.exit(1)
        resultado = restaurar_backup(sys.argv[2])
        for m in resultado:
            print(m)
    else:
        print("Executando backup...")
        resultado = fazer_backup()
        for m in resultado:
            print(f"  {m}")
