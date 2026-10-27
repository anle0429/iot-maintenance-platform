"""Execute the teaching notebook with a real Jupyter kernel, saving native outputs even on failure."""
import argparse,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
import nbformat
from nbclient import NotebookClient

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ml.training_contract import digest,save


def run(args):
    """Preserve an executed notebook and receipt; never turn an error cell into a successful run."""
    notebook=ROOT/'notebooks/01_failure_training.ipynb';target=ROOT/'outputs/notebook-execution'/args.run
    target.mkdir(parents=True,exist_ok=False)
    os.environ.update(IOT_NOTEBOOK_RUN=args.run,IOT_VM_DEADLINE=args.deadline_utc)
    document=nbformat.read(notebook,as_version=4)
    client=NotebookClient(document,timeout=2700,kernel_name='python3',resources={'metadata':{'path':str(ROOT)}})
    started=datetime.now(timezone.utc).isoformat();passed=False
    try:
        client.execute();passed=True
    finally:
        executed=target/'executed.ipynb';nbformat.write(document,executed)
        save(target/'execution.json',{'passed':passed,'run':args.run,'started_utc':started,
            'completed_utc':datetime.now(timezone.utc).isoformat(),'source_notebook_sha256':digest(notebook),
            'executed_notebook_sha256':digest(executed),'code_cells':sum(c.cell_type=='code' for c in document.cells),
            'executed_cells':sum(c.cell_type=='code' and c.execution_count is not None for c in document.cells),
            'error_cells':sum(c.cell_type=='code' and any(o.output_type=='error' for o in c.outputs) for c in document.cells),
            'scope':'Native Jupyter execution of five shared ML stages plus initialization: fresh Feast and two-column labels, frozen split, train/validation selection, saved-model validation and protected fixed-protocol holdout reproduction. No holdout retuning.'})
    from nbconvert import HTMLExporter
    html,_=HTMLExporter().from_notebook_node(document);(target/'executed.html').write_text(html)
    print((target/'execution.json').read_text())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--deadline-utc',required=True)
    run(parser.parse_args())
