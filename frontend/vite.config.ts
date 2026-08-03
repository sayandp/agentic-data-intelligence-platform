import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    // Without this, Vite's "localhost" default can end up bound to only
    // the IPv6 loopback ([::1]:5173) and not IPv4 (127.0.0.1:5173) - a
    // browser/OS that resolves "localhost" to 127.0.0.1 first then can't
    // connect at all, even though the dev server is genuinely running.
    // `true` binds every local address (both families) so it's reachable
    // regardless of which one "localhost" resolves to.
    host: true,
  },
})
