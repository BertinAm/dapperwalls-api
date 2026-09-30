try:
    # PyMySQL stands in for mysqlclient so no C compiler is needed on the host.
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:  # pragma: no cover - only needed when using MySQL
    pass
