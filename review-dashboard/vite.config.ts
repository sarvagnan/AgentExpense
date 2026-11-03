import { createReadStream, statSync } from 'node:fs';
import path from 'node:path';
import {dashboardDataPath} from '../scripts/dashboard-data.mjs';
import { ledgerRoot } from '../scripts/project-paths.mjs';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

const project = ledgerRoot();
const TYPES: Record<string, string> = {
  '.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.heic': 'image/heic',
  '.md': 'text/plain; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.csv': 'text/csv; charset=utf-8', '.json': 'application/json',
  '.xls': 'application/vnd.ms-excel', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

/**
 * Serves ledger evidence during development at the same paths the build links in dist/:
 * /bills/… from ledger/Bills, and /statements/<workbook> for the bank workbooks at the ledger root.
 * Paths are resolved and checked so nothing outside those places can be read.
 */
function evidence(): Plugin {
  return {
    name: 'ledger-evidence',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const url = decodeURIComponent((request.url || '').split('?')[0]);
        let file: string | undefined;
        if (url.startsWith('/data/')) {
          const candidate = path.resolve(dashboardDataPath('public/data'), url.slice('/data/'.length));
          if (candidate.startsWith(dashboardDataPath('public/data') + path.sep) && !url.split('/').some(part=>part.startsWith('.'))) file = candidate;
        } else if (url === '/favicon.svg') file = path.resolve(import.meta.dirname, 'public/favicon.svg');
        else if (url.startsWith('/bills/')) {
          const candidate = path.resolve(project, 'Bills', url.slice('/bills/'.length));
          if (candidate.startsWith(path.join(project, 'Bills') + path.sep)) file = candidate;
        } else if (url.startsWith('/statements/')) {
          const name = url.slice('/statements/'.length);
          if (/^[^/\\]+\.xlsx?$/.test(name)) file = path.join(project, name);
        }
        if (!file) return next();
        try {
          if (!statSync(file).isFile()) return next();
          response.setHeader('Content-Type', TYPES[path.extname(file).toLowerCase()] || 'application/octet-stream');
          createReadStream(file).pipe(response);
        } catch { next(); }
      });
    },
  };
}

export default defineConfig(({ command, isPreview }) => ({
  publicDir: false,
  define: { 'import.meta.env.DASHBOARD_TODAY': JSON.stringify(process.env.DASHBOARD_TODAY || '') },
  base: command === 'build' || isPreview ? '/expenses/' : '/',
  server: {
    host: '127.0.0.1',
    proxy: process.env.EXPENSES_DEV === '1' ? { '/api/': { target: `http://127.0.0.1:${process.env.EXPENSES_PORT || '8701'}` } } : undefined,
    watch: process.env.CODEX_SANDBOX === 'seatbelt'
      ? { useFsEvents: false, usePolling: true }
      : undefined,
  },
  preview: { host: '127.0.0.1' },
  plugins: [react(), evidence()],
}));
