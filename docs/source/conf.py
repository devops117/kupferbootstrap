import os
import sys

sys.path.insert(0, os.path.abspath('../..'))
extensions = [
    'sphinx_click',
    'sphinx.ext.autosummary',  # Create neat summary tables
]
templates_path = ['templates']
project = 'Kupfer👢strap'
html_title = 'Kupferbootstrap'
html_theme = 'furo'
html_static_path = ['static']
html_css_files = ['kupfer_docs.css']
html_favicon = 'static/kupfer-white-filled.svg'
html_theme_options = {
    "globaltoc_maxdepth": 5,
    "globaltoc_collapse": True,
    "light_logo": "kupfer-black-transparent.svg",
    "dark_logo": "kupfer-white-transparent.svg",
}
