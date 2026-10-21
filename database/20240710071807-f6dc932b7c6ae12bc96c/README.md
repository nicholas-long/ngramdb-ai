# data point about file sizes with sorted human readable formats

`run` writes `filesizes.csv`, sorted by descending human-readable disk usage.
Each row contains size, directory, and node title (without a header row).
The title comes from the first Markdown ATX heading (`#` through `######`) in
that directory's `README.md`; missing READMEs or headings produce an empty title.
CSV quoting preserves commas and quotes in titles.
