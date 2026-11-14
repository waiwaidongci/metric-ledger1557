import {request} from './client';
import {LedgerCheck,LedgerImportResult,LedgerBill} from '../types/domain';

export interface LedgerEventInput{bill_no:string;version:number;kind:string;amount:number;occurred_at?:string;memo?:string}

export const getLedgerCheck=()=>request<LedgerCheck>('/ledger/reconciliation');
export const getLedgerBills=()=>request<LedgerBill[]>('/ledger/bills');
export const getLedgerImports=()=>request<LedgerImportResult[]>('/ledger/imports');
export const importLedgerEvents=(data:{batch_key:string;source:string;events:LedgerEventInput[]})=>
  request<LedgerImportResult>('/ledger/imports',{method:'POST',body:JSON.stringify(data)});
