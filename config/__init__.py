# Pilote MySQL : PyMySQL (100 % Python, dans requirements.txt), car mysqlclient ne se compile pas
# chez o2switch. Si mysqlclient est installé (poste de développement), il reste utilisé en priorité.
try:
    import MySQLdb  # noqa: F401
except ImportError:
    try:
        import pymysql
    except ImportError:
        pass
    else:
        pymysql.version_info = (2, 2, 1, 'final', 0)     # version minimale attendue par Django 5.2
        pymysql.install_as_MySQLdb()
