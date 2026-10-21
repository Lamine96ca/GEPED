# Pilote MySQL : mysqlclient (recommandé). S'il n'a pas pu être compilé sur le serveur, PyMySQL
# (100 % Python, `pip install PyMySQL`) le remplace sans autre changement.
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
