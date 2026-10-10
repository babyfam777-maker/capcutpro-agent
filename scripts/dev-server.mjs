import { execFile, spawn } from "node:child_process";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const port = 3000;
const hostname = "127.0.0.1";

function run(command, args) {
  return new Promise((resolve) => {
    execFile(command, args, { encoding: "utf8" }, (error, stdout, stderr) => {
      resolve({
        ok: !error,
        stdout: stdout || "",
        stderr: stderr || "",
      });
    });
  });
}

async function listenTable() {
  const result = await run("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN"]);
  return (result.stdout || result.stderr).trim();
}

async function commandOf(pid) {
  const result = await run("ps", ["-p", String(pid), "-o", "args="]);
  return result.stdout.trim();
}

async function freeStaleDevServers() {
  const listed = await run("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"]);
  const pids = listed.stdout.split(/\s+/).map((value) => value.trim()).filter(Boolean);
  for (const pid of pids) {
    const command = await commandOf(pid);
    const ours = /next[/\\]dist[/\\]bin[/\\]next|scripts[/\\]dev-server\.mjs|eve[/\\]bin[/\\]eve/.test(command);
    if (!ours) {
      console.error(`Port ${port} används redan av en annan process:\n${await listenTable()}`);
      process.exit(1);
    }
    console.error(`Stoppar gammal process på port ${port}: ${pid} ${command}`);
    try {
      process.kill(Number(pid), "SIGTERM");
    } catch {
      // already gone
    }
  }
  if (pids.length > 0) {
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

function ipv6Proxy() {
  const proxy = net.createServer((client) => {
    const upstream = net.connect(port, hostname);
    client.pipe(upstream);
    upstream.pipe(client);
    const close = () => {
      client.destroy();
      upstream.destroy();
    };
    client.on("error", close);
    upstream.on("error", close);
  });
  proxy.on("error", (error) => {
    const code = error && error.code ? error.code : error;
    console.error(`::1:${port} kunde inte öppnas (${code}). http://127.0.0.1:${port} används ändå.`);
  });
  proxy.listen(port, "::1");
  return proxy;
}

function httpOk(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(1500, () => {
      request.destroy();
      resolve(false);
    });
  });
}

await freeStaleDevServers();
const proxy = ipv6Proxy();
const child = spawn(
  process.execPath,
  [path.join(root, "node_modules/next/dist/bin/next"), "dev", "--hostname", hostname, "--port", String(port)],
  {
    cwd: root,
    stdio: "inherit",
    env: { ...process.env, CAPCUT_SKIP_EVE: "1" },
  },
);

let stopped = false;
function stop(signal = "SIGTERM") {
  if (stopped) return;
  stopped = true;
  proxy.close();
  if (child.exitCode === null && child.signalCode === null) child.kill(signal);
}

process.on("SIGINT", () => {
  stop("SIGINT");
  setTimeout(() => process.exit(0), 300);
});
process.on("SIGTERM", () => {
  stop("SIGTERM");
  setTimeout(() => process.exit(0), 300);
});

const deadline = Date.now() + 90_000;
let ready = false;
while (Date.now() < deadline) {
  if (child.exitCode !== null) {
    console.error(`Next.js avslutades med kod ${child.exitCode} innan port ${port} svarade.`);
    console.error(await listenTable());
    stop();
    process.exit(child.exitCode || 1);
  }
  if (await httpOk(`http://${hostname}:${port}/`)) {
    ready = true;
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 300));
}

if (!ready) {
  console.error(`http://${hostname}:${port}/ svarade inte HTTP 200.`);
  console.error(await listenTable());
  stop();
  process.exit(1);
}

const table = await listenTable();
console.log(`\nHTTP 200 från http://${hostname}:${port}/`);
if (table) console.log(table);

child.on("exit", (code, signal) => {
  proxy.close();
  if (signal) process.exit(0);
  process.exit(code ?? 0);
});
