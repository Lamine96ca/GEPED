"""
Point d'entrée de gePed chez o2switch (cPanel → « Setup Python App » → Application startup file).
Passenger charge ce fichier et sert l'objet `application` (Django).
Redémarrer l'application après une mise à jour : `touch tmp/restart.txt` (ou bouton « Restart »).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from config.wsgi import application  # noqa: E402,F401
