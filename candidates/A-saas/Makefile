PY ?= python3
.PHONY: test sim demo
test:
	$(PY) -m unittest discover -s tests -t . -v
sim:
	$(PY) -m sim.model > /dev/null && echo "wrote sim/results.json"
demo:
	rm -rf demo_runs && $(PY) -c "from flakehunter import synth; synth.write('demo_runs', seed=1)"
	$(PY) -m flakehunter analyze 'demo_runs/run*.xml'
