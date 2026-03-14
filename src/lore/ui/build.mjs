import { build } from 'esbuild';

const watch = process.argv.includes('--watch');

const opts = {
  entryPoints: ['src/index.js'],
  bundle: true,
  outfile: 'dist/app.js',
  format: 'iife',
  minify: !watch,
  sourcemap: watch,
  target: ['es2020'],
  logLevel: 'info',
};

if (watch) {
  const ctx = await (await import('esbuild')).context(opts);
  await ctx.watch();
  console.log('Watching for changes...');
} else {
  await build(opts);
}
