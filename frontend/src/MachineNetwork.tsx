import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import type { Machine } from "./hardware-types";
import { jsonRequest, messageFor, requestData } from "./workbench-api";

interface Device { name: string; address: string; port: number }
interface Destination { address: string; port: number; username: string; folder: string; fingerprint: string }
interface Binding extends Destination { machine_id: string; last_file: string | null; last_received: string | null }
const empty = { address: "", port: 22, username: "", folder: "", fingerprint: "" };

export default function MachineNetwork({ machines, onSessionExpired }: {machines: Machine[]; onSessionExpired: () => void}) {
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [machine, setMachine] = useState("");
  const [destination, setDestination] = useState<Destination>(empty);
  const [key, setKey] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [editing, setEditing] = useState(false);
  const binding = bindings.find((item) => item.machine_id === machine);
  const url = `/api/hardware/machines/${machine}/sftp`;

  useEffect(() => {
    const controller = new AbortController();
    requestData<Binding[]>("/api/hardware/network", onSessionExpired, {signal: controller.signal})
      .then(rows => { if (!controller.signal.aborted) setBindings(rows); })
      .catch(error => { if (!controller.signal.aborted) setMessage(messageFor(error)); });
    return () => controller.abort();
  }, [onSessionExpired]);

  async function run(action: () => Promise<void>) {
    setPending(true); setMessage("");
    try { await action(); }
    catch (error) { setMessage(messageFor(error)); }
    finally { setPending(false); }
  }

  function selectMachine(id: string) {
    setMachine(id); setMessage(""); setKey("");
    const row = bindings.find(item => item.machine_id === id);
    setDestination(row ? {address: row.address, port: row.port, username: row.username, folder: row.folder, fingerprint: row.fingerprint} : empty);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      await requestData(url, onSessionExpired, jsonRequest("PUT", destination));
      setBindings(await requestData<Binding[]>("/api/hardware/network", onSessionExpired));
      setEditing(false); setMessage("已连接到机台接收目录。");
    });
  }

  return <section className="panel" aria-label="内网机台">
    <div className="workbench-section-head"><h2>内网机台</h2>
      <button className="secondary-action" type="button" disabled={pending} onClick={() => void run(async () => {
        setDevices(await requestData<Device[]>("/api/hardware/network/discover", onSessionExpired, {method: "POST"}));
      })}>发现内网机台</button>
    </div>
    {pending && <p role="status">正在连接…</p>}
    {devices && (devices.length ? <ul className="plan-items">{devices.map(device => <li key={device.address + ":" + device.port}>
      <button disabled={pending} type="button" onClick={() => {
        selectMachine(""); setDestination({...empty, address: device.address, port: device.port}); setEditing(true);
      }}><strong>{device.name}</strong><span>{device.address}:{device.port}</span></button>
    </li>)}</ul> : <p>未发现公布 SSH 服务的机台。可按地址连接。</p>)}
    {devices && <details><summary>让 Red Hat 机台可被发现</summary>
      <p>机台已启用 Avahi 时，将<a href="/wat-sftp.service" download>服务声明文件</a>放入 <code>/etc/avahi/services/</code>，按实际 SSH 端口修改，然后再次发现机台。</p>
    </details>}
    <ul className="plan-items">{bindings.map(row => <li key={row.machine_id}>
      <button type="button" disabled={pending} onClick={() => { selectMachine(row.machine_id); setEditing(true); }}>
        <strong>{machines.find(m => m.id === row.machine_id)?.name ?? row.address}</strong>
        <span>{row.address} · {row.folder}</span>
        {row.last_file && <span>最近送达（UTC）{row.last_received} · {row.last_file}</span>}
      </button></li>)}</ul>
    {!editing && <button type="button" className="secondary-action" disabled={pending || !machines.length}
      onClick={() => { selectMachine(machines[0].id); setEditing(true); }}>按地址连接</button>}
    {!machines.length && <p>先新增机台档案，再关联接收地址。</p>}
    {editing && <form onSubmit={save}><fieldset disabled={pending} className="workbench-fields">
      <label>机台档案<select value={machine} required onChange={e => {
        const selected = e.target.value;
        const discovered = !machine && destination.address ? {address: destination.address, port: destination.port} : null;
        selectMachine(selected);
        if (discovered) setDestination(current => ({...current, ...discovered}));
      }}><option value="">选择机台</option>{machines.map(m => <option value={m.id} key={m.id}>{m.name}</option>)}</select></label>
      <label>内网 IPv4 地址<input required value={destination.address} onChange={e => setDestination({...destination,address:e.target.value.trim()})}/></label>
      <label>SSH 端口<input required type="number" min="1" max="65535" value={destination.port || ""} onChange={e => setDestination({...destination,port:Number(e.target.value)})}/></label>
      <label>机台接收账号<input required autoComplete="off" value={destination.username} onChange={e => setDestination({...destination,username:e.target.value.trim()})}/></label>
      <label>配方接收目录<input required placeholder="/incoming" value={destination.folder} onChange={e => setDestination({...destination,folder:e.target.value})}/></label>
      <label>机台 SSH 指纹<input required placeholder="SHA256:…" value={destination.fingerprint} onChange={e => setDestination({...destination,fingerprint:e.target.value.trim()})}/></label>
      <button type="button" disabled={!machine} className="secondary-action" onClick={() => void run(async () => {
        setKey((await requestData<{public_key:string}>(url + "/key", onSessionExpired, {method:"POST"})).public_key);
      })}>查看连接公钥</button>
      {key && <label>将此公钥加入机台接收账号的 authorized_keys<textarea readOnly value={key} onFocus={e => e.target.select()}/></label>}
      <div className="workbench-actions"><button className="primary-action" disabled={!machine} type="submit">验证并保存</button>
        {binding && <button type="button" className="secondary-action" onClick={() => {
          if (!window.confirm("解除这台机台与接收目录的关联？")) return;
          void run(async () => { await requestData(url, onSessionExpired, {method:"DELETE"});
            setBindings(bindings.filter(row => row.machine_id !== machine)); setEditing(false); setMessage("已解除关联。"); });
        }}>解除关联</button>}
        <button type="button" className="secondary-action" onClick={() => setEditing(false)}>关闭</button></div>
    </fieldset></form>}
    {message && <p role="status">{message}</p>}
  </section>;
}
