import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from banco import conectar

def validar_dispositivo(dispositivo: str) -> bool:
    """Valida que o dispositivo é um caminho /dev/* válido."""
    if not dispositivo.startswith("/dev/"):
        return False
    if ".." in dispositivo:
        return False
    return True


def validar_ponto_montagem(ponto: str) -> bool:
    """Valida que o ponto de montagem não contém caminhos perigosos."""
    if ".." in ponto:
        return False
    if ponto in {"/", "/etc", "/root", "/sys", "/proc", "/boot"}:
        return False
    return True


def obter_espaco_disponivel(ponto_montagem: str) -> tuple[int, int]:
    """Retorna (usado_mb, disponível_mb) ou (-1, -1) em caso de erro."""
    try:
        resultado = subprocess.run(
            ["df", ponto_montagem],
            capture_output=True,
            text=True,
            check=True
        )
        linhas = resultado.stdout.strip().split('\n')
        if len(linhas) < 2:
            return -1, -1
        partes = linhas[1].split()
        if len(partes) < 4:
            return -1, -1
        total = int(partes[1])
        disponivel = int(partes[3])
        usado = total - disponivel
        return usado, disponivel
    except Exception:
        return -1, -1


def montar_dispositivo(dispositivo: str, ponto_montagem: str) -> tuple[bool, str]:
    """Monta o dispositivo no ponto especificado. Retorna (sucesso, mensagem)."""
    if not validar_dispositivo(dispositivo):
        return False, "Dispositivo inválido"
    if not validar_ponto_montagem(ponto_montagem):
        return False, "Ponto de montagem inválido"

    # Verifica se já está montado
    try:
        resultado = subprocess.run(
            ["grep", ponto_montagem, "/etc/mtab"],
            capture_output=True,
            check=False
        )
        if resultado.returncode == 0:
            return True, "Já montado"
    except Exception:
        pass

    # Cria o ponto de montagem se não existir
    try:
        os.makedirs(ponto_montagem, exist_ok=True)
    except Exception as e:
        return False, f"Erro ao criar ponto de montagem: {str(e)}"

    # Monta via sudo
    try:
        subprocess.run(
            ["sudo", "mount", dispositivo, ponto_montagem],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        return True, "Montado com sucesso"
    except subprocess.TimeoutExpired:
        return False, "Timeout ao montar"
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() if e.stderr else str(e)
        return False, f"Erro ao montar: {msg}"
    except Exception as e:
        return False, f"Erro ao montar: {str(e)}"


def desmontar_dispositivo(ponto_montagem: str) -> tuple[bool, str]:
    """Desmonta o dispositivo do ponto especificado."""
    try:
        subprocess.run(
            ["sudo", "umount", ponto_montagem],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        return True, "Desmontado com sucesso"
    except subprocess.TimeoutExpired:
        return False, "Timeout ao desmontar"
    except subprocess.CalledProcessError as e:
        msg = e.stderr.strip() if e.stderr else str(e)
        return False, f"Erro ao desmontar: {msg}"
    except Exception as e:
        return False, f"Erro ao desmontar: {str(e)}"


def fazer_pg_dump(ponto_montagem: str) -> tuple[bool, str, str]:
    """Faz backup do PostgreSQL via pg_dump. Retorna (sucesso, mensagem, caminho_arquivo)."""
    hoje = datetime.now().strftime("%Y-%m-%d")
    pasta_backup = os.path.join(ponto_montagem, f"shambala_backup_{hoje}")

    try:
        os.makedirs(pasta_backup, exist_ok=True)
    except Exception as e:
        return False, f"Erro ao criar pasta de backup: {str(e)}", ""

    arquivo_sql = os.path.join(pasta_backup, "database.sql")

    try:
        env = os.environ.copy()
        env["PGPASSWORD"] = os.environ.get("SHAMBALA_DB_PASS", "")
        dbname = os.environ.get("SHAMBALA_DB_NAME", "shambala")
        dbuser = os.environ.get("SHAMBALA_DB_USER", "shambala")
        dbhost = os.environ.get("SHAMBALA_DB_HOST", "localhost")
        dbport = os.environ.get("SHAMBALA_DB_PORT", "5432")

        with open(arquivo_sql, "w") as f_out:
            resultado = subprocess.run(
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
                stdout=f_out,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=300
            )

        # Verifica tamanho do arquivo gerado
        if not os.path.exists(arquivo_sql):
            return False, "Arquivo de backup não foi criado", ""

        tamanho = os.path.getsize(arquivo_sql)
        tamanho_mb = tamanho / (1024 * 1024)

        return True, f"Backup criado com sucesso ({tamanho_mb:.2f}MB)", arquivo_sql

    except subprocess.TimeoutExpired:
        return False, "Timeout ao fazer backup do banco", ""
    except subprocess.CalledProcessError as e:
        msg = e.stderr if e.stderr else str(e)
        return False, f"Erro no pg_dump: {msg}", ""
    except Exception as e:
        return False, f"Erro ao fazer backup: {str(e)}", ""


def executar_backup_completo(dispositivo: str, ponto_montagem: str) -> dict:
    """Executa backup completo: monta, faz dump, registra. Retorna dict com status."""
    resultado = {
        "sucesso": False,
        "mensagem": "",
        "tamanho_backup": 0,
        "espaco_disponivel": 0,
        "caminho_backup": ""
    }

    # Monta
    ok, msg = montar_dispositivo(dispositivo, ponto_montagem)
    if not ok:
        resultado["mensagem"] = msg
        return resultado

    # Verifica espaço
    usado, disp = obter_espaco_disponivel(ponto_montagem)
    resultado["espaco_disponivel"] = disp

    if disp > 0 and disp < 100 * 1024:  # Menos de 100MB
        resultado["mensagem"] = f"Espaço insuficiente no pendrive ({disp}MB disponível)"
        desmontar_dispositivo(ponto_montagem)
        return resultado

    # Faz backup
    ok, msg, arquivo = fazer_pg_dump(ponto_montagem)
    resultado["mensagem"] = msg
    resultado["caminho_backup"] = arquivo

    if ok and arquivo and os.path.exists(arquivo):
        resultado["sucesso"] = True
        resultado["tamanho_backup"] = os.path.getsize(arquivo)

    # Desmontar (opcional - mantém montado por padrão para permissões)
    # desmontar_dispositivo(ponto_montagem)

    return resultado


def registrar_backup_historico(status: str, caminho_backup: str, tamanho: int, espaco_disp: int, erro: str = ""):
    """Registra o backup no histórico do banco."""
    try:
        with conectar() as conn:
            conn.execute(
                """INSERT INTO backup_pendrive_historico
                   (status, caminho_backup, tamanho_backup, espaco_disponivel, mensagem_erro)
                   VALUES (%s, %s, %s, %s, %s)""",
                (status, caminho_backup, tamanho, espaco_disp, erro)
            )
    except Exception as e:
        print(f"Erro ao registrar histórico: {str(e)}")


if __name__ == "__main__":
    # Para testes manuais
    dispositivo = "/dev/sdb1"
    ponto = "/mnt/pendrive"
    resultado = executar_backup_completo(dispositivo, ponto)
    print(f"Resultado: {resultado}")
