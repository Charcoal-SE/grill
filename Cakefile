task "build", ->
  fs = require "fs"
  glob = require "glob"

  fs.copyFile "node_modules/jquery/dist/jquery.min.js", "frontend/jquery.js", (err) -> throw err if err
  fs.copyFile "node_modules/bootstrap/dist/css/bootstrap.min.css", "frontend/bootstrap.css", (err) -> throw err if err

  coffee = require "coffeescript"
  uglify = require "uglify-js"

  output = fs.createWriteStream "frontend/build.js"

  glob "frontend/*.coffee", (err, files) ->
    throw err if err

    for file in files
      fs.readFile file, (err, code) ->
        throw err if err

        output.write uglify.minify(coffee.compile code.toString()).code

  output.end

task "run", ->
  invoke "build"
  child_process = require "child_process"

  child_process.spawn "pypy3", ["main.py"], stdio: 'inherit'
