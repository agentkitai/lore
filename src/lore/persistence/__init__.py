"""Server-side persistence layer.

Defines the Store protocol and its backend implementations. The persistence
layer is the only place in the codebase that touches raw SQL or DB drivers.

Names exported by re-export from submodules are added in later tasks.
"""
