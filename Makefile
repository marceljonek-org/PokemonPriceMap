.PHONY: install test scan demo images

install:
	pip install -r requirements.txt

test:
	python -m pytest tests -q

scan:
	python src/scrape.py

images:
	python src/images.py

demo:            ## náhľad stránky zo snapshotov, bez siete
	python tools/demo_from_fixtures.py
	@echo "Otvor docs/index.html cez: python -m http.server -d docs 8000"
