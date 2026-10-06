import { defineConfig } from 'astro/config';
import node from '@astrojs/node';
// 4323 para convivir con barranco-studio (sam3d-barranco/web, 4322). Solo loopback.
export default defineConfig({
  output:'server', adapter:node({mode:'standalone'}),
  server:{host:'127.0.0.1',port:4323},
});
