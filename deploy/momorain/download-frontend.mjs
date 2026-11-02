// 在服务器下载已由 GitHub Actions 编译的 Linux 前端，不在生产机器上跑 webpack/TS。
import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';

const tag = (await readFile('/build/frontend-version.txt', 'utf8')).trim();
if (!/^momorain-web-[a-zA-Z0-9.-]+$/.test(tag)) throw new Error('Invalid frontend release tag');
const base = `https://github.com/momorain-tech/DeepTutor/releases/download/${tag}`;
const name = 'frontend-linux-x64.tar.gz';
const checksum = await fetch(`${base}/${name}.sha256`, { signal: AbortSignal.timeout(120000) });
if (!checksum.ok) throw new Error(`Frontend checksum HTTP ${checksum.status}`);
const expected = (await checksum.text()).trim().split(/\s+/)[0];
if (!/^[a-f0-9]{64}$/.test(expected)) throw new Error('Invalid frontend SHA256');
const response = await fetch(`${base}/${name}`, { signal: AbortSignal.timeout(600000) });
if (!response.ok || !response.body) throw new Error(`Frontend archive HTTP ${response.status}`);
await pipeline(Readable.fromWeb(response.body), createWriteStream('/build/frontend.tar.gz'));
const hash = createHash('sha256');
for await (const chunk of createReadStream('/build/frontend.tar.gz')) hash.update(chunk);
if (hash.digest('hex') !== expected) throw new Error('Frontend SHA256 mismatch');
console.log(`Verified frontend release ${tag}`);
