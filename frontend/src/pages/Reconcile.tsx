import {FormEvent,useMemo,useState} from 'react';
import {useLedgerData} from '../hooks/useLedger';
import {useAuth} from '../stores/auth';
import {useAction} from '../hooks/useAction';
import {importLedgerEvents} from '../api/ledger';
import {Notice} from '../components/common/Notice';
import {DataTable} from '../components/common/DataTable';
import {money,number,percent,time} from '../utils/format';
import {LedgerItem} from '../types/domain';

const STATUS:Record<string,[string,string]>={pending:['待补齐','pending'],source_missing:['来源缺单','missing'],ledger_missing:['账本缺单','missing'],amount_difference:['金额差异','difference'],cancelled:['撤销单','cancelled'],matched:['一致','ok']};

const SAMPLE=`{
  "batch_key": "手动-2026-11-补发",
  "source": "渠道结算补发",
  "events": [
    {"bill_no":"HC-000012","version":2,"kind":"adjustment","amount":1.00,"occurred_at":"2026-10-31","memo":"补齐缺失的第二版"},
    {"bill_no":"HC-000020","version":3,"kind":"adjustment","amount":-1.00,"occurred_at":"2026-10-31","memo":"第二版未到，先保持待补"},
    {"bill_no":"HC-000005","version":2,"kind":"adjustment","amount":5.00,"occurred_at":"2026-10-31","memo":"补记渠道费"}
  ]
}`;

function LedgerRow({item}:{item:LedgerItem}){const [label,tone]=STATUS[item.status]||[item.status,''];return <tr><td className="mono">{item.bill_no}</td><td><span className={`check-status ${tone}`}>{label}</span></td><td>{item.channel?`${item.channel} / ${item.month}`:'—'}</td><td>{money(item.source_reference??undefined)}</td><td>{money(item.ledger_amount??undefined)}</td><td className={item.difference?"difference":undefined}>{item.difference==null?'—':money(item.difference)}</td><td>v{item.applied_version} / {item.version_count}{item.pending_versions>0&&<small> 待补 {item.pending_versions}</small>}</td></tr>;}

export function Reconcile(){
  const s=useLedgerData(),{user}=useAuth(),action=useAction();
  const readOnly=user?.role==='viewer';
  const [batchKey,setBatchKey]=useState('');const [payload,setPayload]=useState(SAMPLE);
  const [receipt,setReceipt]=useState('');
  const check=s.ledger,summary=check?.summary;
  const flagged=useMemo(()=>[...(check?.pending||[]),...(check?.amount_differences||[]),...(check?.source_missing||[]),...(check?.ledger_missing||[]),...(check?.cancelled||[])], [check]);
  function submit(e:FormEvent){
    e.preventDefault();setReceipt('');
    action.execute(async()=>{
      let parsed:unknown;
      try{parsed=JSON.parse(payload)}catch{throw new Error('导入内容不是合法 JSON，请检查括号与引号')}
      const body=parsed as {batch_key?:string;source?:string;events:unknown[]};
      if(!body||!Array.isArray(body.events))throw new Error('导入 JSON 需要包含 events 数组');
      const result=await importLedgerEvents({batch_key:batchKey.trim()||body.batch_key||'',source:body.source||'来源核对页手动导入',events:body.events as never});
      setReceipt(`批次 ${result.batch_key}：写入 ${result.inserted_count}，重复 ${result.duplicate_count+(result.batch_duplicates||0)}，待补账单 ${result.pending_count}，影响 ${result.bills_affected} 单${result.replayed?'（重放已受理批次）':''}`);
      await s.refresh();
    });
  }
  return <div className="page">
    <div className="page-title"><div><p className="eyebrow">SOURCE RECONCILIATION / 03</p><h1>来源核对<span>参考账本差异要有落点，独立账本要能双向对上。</span></h1></div><button className="outline" onClick={s.refresh}>重新核对</button></div>
    <Notice message={s.error||action.error} retry={s.refresh}/>
    {s.reconciliation&&<><div className="reconcile-summary"><div><span>参考账本净收入</span><strong>{money(s.reconciliation.total.reference_net)}</strong></div><div><span>来源重算净收入</span><strong>{money(s.reconciliation.total.canonical_net)}</strong></div><div><span>总差异 / {s.reconciliation.affected_cells} 个单元</span><strong className="difference">{money(s.reconciliation.total.difference)}</strong></div></div><div className="section-heading"><h2>差异归因</h2><span>按绝对差异排序 / 阈值 0.01 元</span></div><DataTable head={['渠道','月份','来源重算 / 元','参考账本 / 元','差异 / 元','相对差异','明细数']}>{s.reconciliation.attribution.length?s.reconciliation.attribution.map((c,i)=><tr key={i}><td>{c.dimensions.channel}</td><td>{c.dimensions.month}</td><td>{number(c.canonical_net)}</td><td>{number(c.reference_net)}</td><td className="difference">{number(c.difference)}</td><td>{percent(c.relative_difference)}</td><td>{c.source_count}</td></tr>):<tr><td colSpan={7}>本次无超阈值差异，可继续生成交付报表。</td></tr>}</DataTable><p className="caption">{s.reconciliation.reconciliation_rule}</p></>}

    <div className="section-heading"><h2>独立账本 · 双向核对</h2><span>账单编号对应来源编号 / 金额精确到分</span></div>
    {summary&&<div className="ledger-summary">
      <div><span>账本金额</span><strong>{money(summary.ledger_total)}</strong></div>
      <div><span>来源参考净额</span><strong>{money(summary.source_reference_total)}</strong></div>
      <div><span>差异合计</span><strong className="difference">{money(summary.difference_total)}</strong></div>
      <div><span>待补齐 / 缺单</span><strong>{summary.pending} / {summary.source_missing+summary.ledger_missing}</strong></div>
    </div>}
    <div className="ledger-split">
      <div>
        <div className="section-heading top"><h2>待补与差异</h2><span>{flagged.length} 张账单需要跟进</span></div>
        <DataTable head={['账单 / 来源编号','状态','渠道 / 月份','来源参考 / 元','账本金额 / 元','差额 / 元','进度']}>{flagged.length?flagged.map(item=><LedgerRow key={item.bill_no+item.status} item={item}/>):<tr><td colSpan={7}>账本与来源均已对齐，没有待补或差异账单。</td></tr>}</DataTable>
        <p className="caption">{check?.rule}</p>
      </div>
      <section className="ledger-import">
        <p className="eyebrow">LEDGER IMPORT</p>
        <h2>导入账本事件</h2>
        <p className="caption">原单 v1 → 差额更正 → 撤销；后序先到时保持待补，前序到达后连续应用。重复版本不重复改金额，同版本异文整批拒绝。历史交付仍显示原快照。</p>
        {readOnly?<p className="role-note">渠道阅读者可查看核对结果，导入由分析师或口径负责人执行。</p>:
        <form onSubmit={submit}>
          <label>批次标识<input aria-label="批次标识" value={batchKey} onChange={e=>setBatchKey(e.target.value)} placeholder="留空按内容生成，重放同一批次请填写相同标识"/></label>
          <label>事件 JSON<textarea aria-label="账本事件 JSON" value={payload} onChange={e=>setPayload(e.target.value)} rows={12} spellCheck={false}/></label>
          <button className="primary" disabled={action.busy}>{action.busy?'正在应用…':'导入并核对'}</button>
        </form>}
        {receipt&&<div className="receipt"><b>导入回执</b><p>{receipt}</p></div>}
      </section>
    </div>

    <div className="section-heading"><h2>批次处理记录</h2><span>中断后由服务重启重放未完成批次</span></div>
    <DataTable head={['批次','状态','收到 / 写入','重复','待补账单','影响账单','尝试','处理时间']}>{s.imports.length?s.imports.map(row=><tr key={row.batch_key}><td className="mono">{row.batch_key}</td><td><span className={`check-status ${row.status==='completed'?'ok':row.status==='rejected'?'difference':'pending'}`}>{row.status==='completed'?'已完成':row.status==='rejected'?'已拒绝':'处理中'}</span>{row.rejected_reason&&<small> {row.rejected_reason}</small>}</td><td>{row.received_count} / {row.inserted_count}</td><td>{row.duplicate_count}</td><td>{row.pending_count}</td><td>{row.bills_affected}</td><td>{row.attempts}</td><td>{row.completed_at?time(row.completed_at):'—'}</td></tr>):<tr><td colSpan={8}>还没有账本批次，先在右侧导入一组事件。</td></tr>}</DataTable>

    <div className="section-heading"><h2>原始结算明细</h2><span>hengchi-settlement-202611.csv / 前100条</span></div>
    <DataTable head={['来源编号','日期','渠道 / 区域','收入 / 元','退款 / 元','成本 / 元','单数']}>{s.sources.map(row=><tr key={row.source_key}><td className="mono">{row.source_key}</td><td>{row.day}</td><td>{row.channel} / {row.region}</td><td>{number(row.revenue)}</td><td>{number(row.refunds)}</td><td>{number(row.cost)}</td><td>{row.orders}</td></tr>)}</DataTable>
  </div>;
}
