import fs from 'node:fs';
import { PNG } from 'pngjs';
import jsQR from 'jsqr';
const file=process.argv[2];
const expected=process.argv[3];
const img=PNG.sync.read(fs.readFileSync(file));
const qr=jsQR(new Uint8ClampedArray(img.data),img.width,img.height);
if (!qr || qr.data !== expected) throw new Error('QR destination mismatch or unreadable');
console.log(`PASS ${img.width}x${img.height}, QR: ${qr.data}`);
