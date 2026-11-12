.PHONY: eval eval-fresh eval-cached

eval:
	python -m eval.run_eval

eval-fresh:
	python -m eval.run_eval --fresh-judge

eval-cached:
	python -m eval.run_eval --cached-only
