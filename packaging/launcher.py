"""Absolute package entry point for the standalone app."""
import sys

if len(sys.argv)>1 and sys.argv[1]=='--smoke-test':
    from frameglass.packaging_check import main
else:
    from frameglass.app import main

if __name__=='__main__':
    main()
