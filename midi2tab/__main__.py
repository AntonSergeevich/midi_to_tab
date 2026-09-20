"""python -m midi2tab -- запуск окна, с аргументом -- командная строка."""

import sys

if len(sys.argv) > 1:
    from .cli import main

    raise SystemExit(main())
else:
    from .gui import main

    main()
