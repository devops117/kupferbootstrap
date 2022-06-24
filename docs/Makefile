buildargs := -b dirhtml -aE source html

.PHONY: cleanbuild clean

cleanbuild:
	@make clean
	@make html

clean:
	rm -rf html source/cli

html:
	sphinx-build $(buildargs)

serve: html
	(cd html && python -m http.server 9999)
