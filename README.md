# pdt-anes-cdf

Metadata for the [ANES Time Series Cumulative Data File](https://electionstudies.org/) (CDF), built to the [PublicDataToolkit](https://github.com/AllenDowney/PublicDataToolkit) standard.

**Status:** a 32-variable slice of the February 5, 2026 release, built for the PublicDataToolkit spike.

The original dataset is the [ANES Time Series Cumulative Data File](https://electionstudies.org/data-center/anes-time-series-cumulative-data-file/) at electionstudies.org. This repository is derived from it and is not produced or endorsed by ANES.

## What the dataset is

The CDF pools questions asked in three or more American National Election Studies Time Series studies, 1948 to 2024, with codes harmonized across years.

## Terms of use

ANES asks users to use its data only for research or statistical purposes, to make no use of respondents' identities, and to cite ANES data and documentation ([terms of use](https://electionstudies.org/data-center/)). ANES public release datasets are public goods, and datasets derived from them may be shared with a link back to the original ([FAQ](https://electionstudies.org/faq/)). The terms as reviewed are in [`dataset.yaml`](dataset.yaml).

## How to get the data

Download the CDF from [electionstudies.org](https://electionstudies.org/) in a browser (no login needed); [`sources.yaml`](sources.yaml) gives the files and checksums. The site blocks scripted downloads.

## The metadata

- [`metadata/releases/20260205/variables.jsonl`](metadata/releases/20260205/variables.jsonl): one record per variable: label, question text (each wording, with the years it applies to), codes with the codebook's valid/missing split and typed missing values, years asked, notes, and where each fact came from.
- [`metadata/derived/scale_signature.jsonl`](metadata/derived/scale_signature.jsonl): response formats (thermometer, favor/oppose).

## How to cite

American National Election Studies. 2026. ANES Time Series Cumulative Data File [dataset and documentation]. February 5, 2026 version. www.electionstudies.org

## How to rebuild

With the sources in `data/raw/` and a PublicDataToolkit checkout next to this repo:

```bash
make build
make check
```

`build/report.md` lists what the build checked against the data.

## License

Code here is under the MIT License. Our metadata, corrections and generated views are under CC BY 4.0. The publisher's files and documentation keep the publisher's terms (above). See [`LICENSE`](LICENSE).
