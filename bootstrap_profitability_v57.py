from __future__ import annotations
import argparse, json


def main():
    p=argparse.ArgumentParser(description='V57 execution-first bootstrap after Supabase migration')
    p.add_argument('--limit',type=int,default=20000)
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args()
    from backfill_execution_dataset_v57 import backfill
    result={'backfill':backfill(args.limit,args.dry_run)}
    if args.dry_run:
        print(json.dumps(result,ensure_ascii=False,indent=2)); return
    from build_profit_profile import rebuild_from_supabase
    result['profile']=rebuild_from_supabase(limit=args.limit)
    from execution_model_v57 import train
    result['execution_model']=train(trigger='v57_bootstrap')
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))

if __name__=='__main__': main()
