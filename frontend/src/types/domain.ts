export interface User{name:string;email:string;role:string}
export interface Metric{id:number;key:string;name:string;expression:string;unit:string;description:string;owner:string;dependencies:string[]}
export interface SourceRow{source_key:string;day:string;channel:string;region:string;revenue:number;cost:number;refunds:number;orders:number;reference_net:number}
export interface Cell{dimensions:Record<string,string>;values:Record<string,number>;reference_net:number;canonical_net:number;difference:number;relative_difference:number|null;source_count:number}
export interface ReportResult{dimensions:string[];metrics:Metric[];evaluation_order:string[];dependencies:Record<string,string[]>;cells:Cell[];total:Cell;affected_cells:number;attribution:Cell[];source_checksum:string;reconciliation_rule:string}
export interface ReportRun{id:number;name:string;dimensions:string[];status:string;result:ReportResult;error:string;created_at:string;owner:string;schedule_id:number|null}
export interface Schedule{id:number;name:string;dimensions:string[];interval_minutes:number;next_run_at:string;active:boolean;owner:string}
export interface Delivery{id:number;schedule_id:number;run_id:number;title:string;created_at:string}
export interface Audit{id:number;actor:string;action:string;target:string;created_at:string}

export interface LedgerItem{bill_no:string;status:string;source_exists:boolean;ledger_exists:boolean;source_reference:number|null;ledger_amount:number|null;difference:number|null;applied_version:number;version_count:number;pending_versions:number;cancel_version:number|null;channel:string|null;month:string|null;region:string|null;orders:number|null}
export interface LedgerSummary{bills:number;sources:number;matched:number;pending:number;source_missing:number;ledger_missing:number;amount_difference:number;cancelled:number;ledger_total:number;source_reference_total:number;difference_total:number;pending_versions:number}
export interface LedgerCheck{summary:LedgerSummary;pending:LedgerItem[];source_missing:LedgerItem[];ledger_missing:LedgerItem[];amount_differences:LedgerItem[];cancelled:LedgerItem[];matched_sample:LedgerItem[];rule:string}
export interface LedgerBill{bill_no:string;status:string;amount:number;applied_version:number;version_count:number;pending_versions:number;cancel_version:number|null;updated_at:string}
export interface LedgerImportBill{bill_no:string;applied_version:number;amount:number;status:string;pending_versions:number;cancel_version:number|null}
export interface LedgerImportResult{batch_key:string;status:string;content_hash:string;received_count:number;batch_duplicates:number;inserted_count:number;duplicate_count:number;pending_count:number;applied_advance:number;bills_affected:number;attempts:number;rejected_reason:string;replayed:boolean;bills:LedgerImportBill[];pending_versions:number;created_at:string;completed_at:string}
