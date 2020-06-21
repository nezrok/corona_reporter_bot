TEST_CMD = python3 -m doctest
CHECKSTYLE_CMD = flake8 --max-line-length=100

all: compile test checkstyle

compile:
	@echo "Nothing to compile for Python"

test:
	$(TEST_CMD) *.py **/*.py

checkstyle:
	$(CHECKSTYLE_CMD) *.py **/*.py

clean:
	rm -f *.pyc
	rm -f *.xlsx
	rm -rf __pycache__

start:
	python3 corona_reporter_bot.py
