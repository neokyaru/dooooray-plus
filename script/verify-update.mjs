// Verify signed public artifacts only. Never starts the installer or product.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(process.argv[2]||fileURLToPath(new URL('..',import.meta.url)));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const key=await regular('update-public.pem');
assert.equal(sha(key),'f0bcfad3beb4de9493c4bad78d3725b5cf90e79f7da8efa36b34db1a3f53fdec','Trusted public key changed');
async function regular(relative){const full=path.join(root,relative);for(let current=full;;current=path.dirname(current)){const s=await fs.lstat(current);assert(!s.isSymbolicLink(),'Linked artifact path');if(current===full)assert(s.isFile(),'Artifact must be a regular file');if(current===root)break;assert(current.startsWith(root+path.sep),'Artifact outside checkout')}return fs.readFile(full)}
function signed(bytes){const envelope=JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/,''));assert(crypto.verify(null,Buffer.from(JSON.stringify(envelope.manifest)),key,Buffer.from(envelope.signature||'','base64')),'Invalid update signature');return envelope.manifest}
const latest=await regular('update/v2/latest.json');assert((await regular('update/latest.json')).equals(latest),'Patch aliases differ');
const m=signed(latest);assert.equal(m.schema,2);assert.equal(m.bootProtocol,1);assert.match(m.version,/^\d{1,4}\.\d{1,4}\.\d{1,4}$/);assert.equal(m.archive,'theme-'+m.version+'.zip');
const archive=await regular('update/v2/'+m.archive);assert.equal(archive.length,m.size);assert.equal(sha(archive),m.sha256);assert((await regular('update/'+m.archive)).equals(archive),'Archive aliases differ');
const setup=signed(await regular('update/v2/installer.json'));assert.equal(setup.schema,1);assert.equal(setup.version,m.version);assert.equal(setup.file,'DoorayPlus-'+m.version+'-Setup.exe');assert.equal(typeof setup.required,'boolean');assert.equal(setup.installLayout,'independent-user-v1');
const executable=await regular('update/v2/'+setup.file);assert(executable.length<=100*1024*1024);assert.equal(executable.length,setup.size);assert.equal(sha(executable),setup.sha256);
console.log('PASS signed patch '+m.version+', aliases, archive and optional installer hashes. No executable was run.');
