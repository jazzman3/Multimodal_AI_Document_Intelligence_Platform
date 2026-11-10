"""Deterministic synthetic evaluation; not a real-world accuracy estimate."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from docintel.core import read_document, extract_rules, validate_fields
from tests.test_core import INVOICE,CONTRACT,FORM,pdf,image_bytes

ROOT=Path(__file__).resolve().parent
SAMPLES={'invoice':INVOICE,'contract':CONTRACT,'form':FORM}
CASES=[]
for kind,text in SAMPLES.items():
    expected=extract_rules(text,kind)[0]
    for format_name,filename,data in [
        ('digital_pdf',f'{kind}_digital.pdf',pdf(text)),
        ('scanned_pdf',f'{kind}_scan.pdf',pdf(text,True)),
        ('image_png',f'{kind}.png',image_bytes(text))]:
        CASES.append((kind,format_name,filename,data,expected))

def evaluate(write=True):
    results=[]; ncorrect=0; nfields=0; full=0
    for kind,mode,filename,data,expected in CASES:
        start=time.perf_counter()
        extracted,meta=read_document(filename,data)
        actual=extract_rules(extracted,kind)[0]
        per_field={field:(actual.get(field)==value) for field,value in expected.items()}
        correct=sum(per_field.values()); total=len(per_field)
        ncorrect+=correct;nfields+=total
        full+=int(correct==total)
        results.append({'kind':kind,'format':mode,'correct':correct,'total':total,
                        'exact_match':correct==total,'field_matches':per_field,
                        'expected':expected,'predicted':actual,'processing_ms':round((time.perf_counter()-start)*1000,3),'metadata':meta})
    summary={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic, machine-generated single-page samples; offline rules+OCR only',
             'documents':len(results),'fields':nfields,'correct_fields':ncorrect,
             'field_exact_match_rate':round(ncorrect/nfields,4),'document_exact_match_rate':round(full/len(results),4),
             'by_format':{},'cases':results}
    for mode in ('digital_pdf','scanned_pdf','image_png'):
        rows=[r for r in results if r['format']==mode]
        summary['by_format'][mode]={'documents':len(rows),'field_accuracy':round(sum(r['correct'] for r in rows)/sum(r['total'] for r in rows),4),
                  'document_exact_match':round(sum(r['exact_match'] for r in rows)/len(rows),4)}
    if write:
        (ROOT/'synthetic_evaluation.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    return summary

if __name__=='__main__':
    s=evaluate();print(json.dumps({k:v for k,v in s.items() if k!='cases'},indent=2))
