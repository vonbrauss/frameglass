"""Use bundled trust roots when launched outside a Python installation."""
import os
import certifi
os.environ.setdefault('SSL_CERT_FILE',certifi.where())
